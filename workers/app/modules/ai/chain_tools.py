"""Read-only on-chain lookups backed by the wired algod node (ALGOD_URL/TOKEN, the same connector chain_reader.py uses). These answer the recurring "verify it on-chain" gap the writer kept working around — point lookup_application at a governance app to read its live proposal/vote state, etc.

algod gives CURRENT state only (no history — that needs an indexer). Every handler
is failure-tolerant: any error returns {"error": ...} and never aborts the article.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0


def _algod_get(path: str) -> dict[str, Any]:
    """GET a path off the operator-configured (trusted) algod node. Returns the parsed JSON, {"_status": 404} for a missing entity, or {"error": ...}."""
    import httpx

    from app.core.config import ALGOD_TOKEN, ALGOD_URL

    if not ALGOD_URL:
        return {"error": "algod not configured (ALGOD_URL unset)"}
    headers = {"X-Algo-API-Token": ALGOD_TOKEN} if ALGOD_TOKEN else {}
    try:
        with httpx.Client(timeout=_TIMEOUT) as http:
            r = http.get(f"{ALGOD_URL}{path}", headers=headers)
        if r.status_code == 404:
            return {"_status": 404}
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _encode_address(pubkey: bytes) -> str:
    """32-byte public key -> 58-char Algorand address (base32 of key + 4-byte sha512/256 checksum)."""
    chksum = hashlib.new("sha512_256", pubkey).digest()[-4:]
    return base64.b32encode(pubkey + chksum).decode("ascii").rstrip("=")


def _decode_key(b64key: str) -> str:
    """TEAL state keys are arbitrary bytes — show printable ASCII as-is, else hex."""
    try:
        raw = base64.b64decode(b64key)
    except Exception:
        return b64key
    if raw and all(32 <= c < 127 for c in raw):
        return raw.decode("ascii")
    return "0x" + raw.hex()


def _decode_value(v: dict) -> int | str:
    """TEAL state value: type 2 = uint, type 1 = bytes (shown as ASCII, a 32-byte address, or hex — best-effort so the model can read governance/admin fields)."""
    if v.get("type") == 2:
        return v.get("uint", 0)
    try:
        raw = base64.b64decode(v.get("bytes", ""))
    except Exception:
        return v.get("bytes", "")
    if raw and all(32 <= c < 127 for c in raw):
        return raw.decode("ascii")
    if len(raw) == 32:
        try:
            return _encode_address(raw)
        except Exception:
            logger.debug("failed to encode 32-byte value as address; falling back to hex")
    return "0x" + raw.hex()


def _is_valid_address(addr: str) -> bool:
    """58-char Algorand address format + checksum validity — the inverse of _encode_address. Rejects a fabricated address BEFORE it reaches algod, with a clear reason, instead of algod's generic 400 (root-caused 2026-07-14: the model invented plausible-looking addresses like 'EXA6RX5G...' for projects it had no real address for — three of the four weren't even the right length, and none had a chance of a valid checksum since nothing was actually generated)."""
    if len(addr) != 58:
        return False
    try:
        raw = base64.b32decode(addr + "=" * ((8 - len(addr) % 8) % 8), casefold=True)
    except Exception:
        return False
    if len(raw) != 36:
        return False
    pubkey, chksum = raw[:32], raw[32:]
    return hashlib.new("sha512_256", pubkey).digest()[-4:] == chksum


_INVALID_ADDRESS_ERROR = (
    "not a valid Algorand address (wrong length or bad checksum). Only call "
    "this with an address you actually found in a fetched page, search "
    "result, or another tool's output — never construct, guess, or "
    "pattern-match one yourself, even a plausible-looking 'vanity' one."
)


def _tool_lookup_account(address: str) -> dict[str, Any]:
    """Live state of an Algorand account: ALGO balance, ASAs held, the apps it created or opted into, its rekey state, and signature type.

    auth_addr (2026-08-05, root-caused live): algod's response carries this
    field for a REKEYED account (one that delegated signing authority to a
    different address) but it used to be silently discarded. This is real
    evidence of common control even when two accounts have different
    addresses -- caught live comparing two NFT-collection creator addresses
    that turned out to share the same auth-addr, meaning the same real
    signer controls both despite looking unrelated by address alone. None
    when the account has never been rekeyed (auth-addr == the account's own
    address, algod omits the field in that case).
    """
    addr = (address or "").strip()
    if not addr:
        return {"error": "address required"}
    if not _is_valid_address(addr):
        return {"address": addr, "error": _INVALID_ADDRESS_ERROR}
    data = _algod_get(f"/v2/accounts/{addr}")
    if not isinstance(data, dict):
        return {"error": "unexpected algod response"}
    if data.get("error"):
        return data
    if data.get("_status") == 404:
        return {"address": addr, "error": "account not found"}
    assets = data.get("assets", []) or []
    created_assets = data.get("created-assets", []) or []
    created_apps = data.get("created-apps", []) or []
    opted_in_apps = data.get("apps-local-state", []) or []
    return {
        "address": addr,
        "balance_algo": round((data.get("amount", 0) or 0) / 1e6, 6),
        "min_balance_algo": round((data.get("min-balance", 0) or 0) / 1e6, 6),
        "status": data.get("status"),
        "auth_addr": data.get("auth-addr"),
        # sig_type distinguishes a personal wallet (sig) from a multisig
        # (msig, shared control across several signers) or a logicsig
        # (lsig, contract-controlled) -- absent if this address has never
        # sent a transaction. Real signal for "who/what actually controls
        # this account," same investigative use as auth_addr.
        "sig_type": data.get("sig-type"),
        "total_assets_held": len(assets),
        "assets": [
            {"asset_id": a.get("asset-id"), "amount": a.get("amount")}
            for a in assets[:25]
            if isinstance(a, dict)
        ],
        # True counts alongside the 25-item-capped lists below, so a story
        # about a prolific creator doesn't quietly look like a small one
        # just because the tool only lists a slice.
        "total_created_assets": len(created_assets),
        "total_created_apps": len(created_apps),
        "total_apps_opted_in": len(opted_in_apps),
        "created_assets": [
            a.get("index") for a in created_assets[:25] if isinstance(a, dict)
        ],
        "created_apps": [a.get("id") for a in created_apps[:25] if isinstance(a, dict)],
        "opted_in_apps": [
            a.get("id")
            for a in opted_in_apps[:25]
            if isinstance(a, dict)
        ],
    }


def _asset_total_adjusted(p: dict[str, Any]) -> tuple[Any, Any, float | None]:
    """(total, decimals, total_adjusted) from an ASA's indexer/algod `params` dict.

    total/decimals are raw base units — division-by-10**decimals done here,
    not left to the model. Doing it in-prompt is exactly how a real incident
    happened (2026-07-14): the writer manually converted a 15-digit raw ASA
    total and got the decimal shift wrong, reporting "1 trillion" for what
    total_adjusted here correctly computes as 1 billion. Shared by
    lookup_asset and lookup_asset_by_name so a supply figure is computed the
    same safe way regardless of which tool surfaced the asset.
    """
    total = p.get("total")
    decimals = p.get("decimals")
    total_adjusted = None
    if isinstance(total, int | float) and isinstance(decimals, int) and decimals >= 0:
        total_adjusted = round(total / (10**decimals), 6)
    return total, decimals, total_adjusted


def _tool_lookup_asset(asset_id: int | str) -> dict[str, Any]:
    """Algorand Standard Asset (ASA) parameters: name, supply, decimals, creator, the manager/freeze/clawback/reserve roles, and its metadata hash.

    metadata_hash (2026-08-05): the on-chain commitment an ARC-3/ARC-19 NFT's
    metadata is supposed to match (base64, 32 raw bytes) -- lets a
    verification actually CHECK a fetched metadata JSON against what's
    committed on-chain instead of trusting it blindly. None for assets that
    don't set one (most fungible tokens).
    """
    aid = str(asset_id).strip()
    if not aid.isdigit():
        return {"error": "asset_id must be a numeric ASA id"}
    data = _algod_get(f"/v2/assets/{aid}")
    if not isinstance(data, dict):
        return {"error": "unexpected algod response"}
    if data.get("error"):
        return data
    if data.get("_status") == 404:
        return {"asset_id": int(aid), "error": "asset not found"}
    p = data.get("params", {}) or {}
    total, decimals, total_adjusted = _asset_total_adjusted(p)
    return {
        "asset_id": int(aid),
        "name": p.get("name"),
        "unit_name": p.get("unit-name"),
        "total": total,
        "decimals": decimals,
        "total_adjusted": total_adjusted,
        "creator": p.get("creator"),
        "url": p.get("url"),
        "metadata_hash": p.get("metadata-hash"),
        "default_frozen": p.get("default-frozen"),
        "manager": p.get("manager"),
        "freeze": p.get("freeze"),
        "clawback": p.get("clawback"),
        "reserve": p.get("reserve"),
    }


def _mainnet_idx_get(path: str, params: dict | None = None) -> dict[str, Any]:
    """GET a path off the public MAINNET indexer (name-search capable, unlike algod). Returns parsed JSON, {"_status": 404} for a missing entity, or {"error": ...}."""
    import httpx

    from app.core.config import MAINNET_INDEXER_URL

    if not MAINNET_INDEXER_URL:
        return {"error": "mainnet indexer not configured (MAINNET_INDEXER_URL unset)"}
    try:
        with httpx.Client(timeout=_TIMEOUT) as http:
            r = http.get(f"{MAINNET_INDEXER_URL}{path}", params=params)
        if r.status_code == 404:
            return {"_status": 404}
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _tool_lookup_asset_by_name(name: str, limit: int = 5) -> dict[str, Any]:
    """Search mainnet ASAs by ticker/unit-name (preferred) or display name when the numeric asset_id isn't known yet.

    lookup_asset needs an id, and algod itself can't search by name (only the
    indexer can). Each result already includes decimal-corrected supply
    (total/decimals/total_adjusted) from the same indexer response, so a
    separate lookup_asset call is only needed for fields this doesn't return
    (manager/freeze/clawback/reserve, metadata url) — root-caused 2026-08-05:
    this used to return identity fields only, and a compose that found an
    asset by name here never made the follow-up call, so it called a
    perfectly knowable supply "undisclosed."

    Root-caused 2026-07-16: a ticker query (e.g. "WAD") used to hit the
    indexer's `name` param, which substring-matches a project's free-text
    DISPLAY name — but a real stablecoin's display name may not contain its
    own ticker at all ("Whale Asset Dollar" doesn't contain "wad"), so its own
    token was invisible to this search while unrelated spam/airdrop tokens
    with garbled names ("32353024;WADIWYER") matched purely by coincidence. A
    compose cited that spam asset's id as the real token's — the model wasn't
    hallucinating a number, it was quoting this tool's top (and only
    plausible-looking) "result" verbatim.

    Fix: query the indexer's `unit` param (substring-matches unit-name, the
    actual ticker) over a wide page, then rank an EXACT case-insensitive
    unit-name match first — real tokens surface at the top instead of getting
    lost in substring noise. Falls back to the old `name` search only when the
    ticker search finds nothing at all (a genuine display-name lookup, e.g.
    "CompX").
    """
    q = (name or "").strip()
    if not q:
        return {"error": "name must not be empty"}
    n = max(1, min(int(limit), 20))
    data = _mainnet_idx_get("/v2/assets", params={"unit": q, "limit": 100})
    if not isinstance(data, dict):
        return {"error": "unexpected indexer response"}
    if data.get("error"):
        return data
    assets = data.get("assets", []) or []
    if not assets:
        data = _mainnet_idx_get("/v2/assets", params={"name": q, "limit": n})
        if not isinstance(data, dict):
            return {"error": "unexpected indexer response"}
        if data.get("error"):
            return data
        assets = data.get("assets", []) or []

    def _rank(a: dict[str, Any]) -> int:
        unit = ((a.get("params") or {}).get("unit-name") or "").strip().upper()
        return 0 if unit == q.upper() else 1

    assets = sorted(assets, key=_rank)
    results = []
    for a in assets:
        if not isinstance(a, dict):
            continue
        p = a.get("params", {}) or {}
        total, decimals, total_adjusted = _asset_total_adjusted(p)
        results.append(
            {
                "asset_id": a.get("index"),
                "name": p.get("name"),
                "unit_name": p.get("unit-name"),
                "creator": p.get("creator"),
                "total": total,
                "decimals": decimals,
                "total_adjusted": total_adjusted,
            }
        )
    return {"query": q, "results": results[:n]}


def _find_incoming_funder(transactions: list[Any], addr: str) -> dict[str, Any] | None:
    """First transaction in `transactions` that pays/transfers INTO `addr` (not sent by it), formatted as a funding result — or None if none of them are incoming."""
    for t in transactions:
        if not isinstance(t, dict) or t.get("sender") == addr:
            continue  # this account paying OUT at its own creation round isn't its funder
        pay = t.get("payment-transaction") or {}
        axfer = t.get("asset-transfer-transaction") or {}
        if pay.get("receiver") != addr and axfer.get("receiver") != addr:
            continue
        return {
            "funder": t.get("sender"),
            "txid": t.get("id"),
            "tx_type": t.get("tx-type"),
            "amount_microalgo": pay.get("amount"),
            "asset_id": axfer.get("asset-id"),
            "asset_amount": axfer.get("amount"),
        }
    return None


def _tool_lookup_first_funding(address: str) -> dict[str, Any]:
    """Who first funded this Algorand account, via the mainnet indexer.

    Added 2026-08-05 (owner request), same investigative thread as
    auth_addr: two accounts can look unrelated by creator address alone but
    share a common funder, which is real evidence worth checking. Uses the
    account's own created-at-round (the indexer tracks this natively) to
    query transactions at EXACTLY that round rather than paging through full
    history — the transaction that funded the account and the one that
    created it are the same event.
    """
    addr = (address or "").strip()
    if not _is_valid_address(addr):
        return {"address": addr, "error": _INVALID_ADDRESS_ERROR}
    acct = _mainnet_idx_get(f"/v2/accounts/{addr}")
    if not isinstance(acct, dict):
        return {"error": "unexpected indexer response"}
    if acct.get("error"):
        return acct
    if acct.get("_status") == 404:
        return {"address": addr, "found": False, "error": "account not found on mainnet"}
    created_round = (acct.get("account") or {}).get("created-at-round")
    if created_round is None:
        return {"address": addr, "found": False, "error": "no created-at-round available"}
    txns = _mainnet_idx_get(
        f"/v2/accounts/{addr}/transactions",
        params={"min-round": created_round, "max-round": created_round, "limit": 20},
    )
    if not isinstance(txns, dict):
        return {"error": "unexpected indexer response"}
    if txns.get("error"):
        return txns
    funding = _find_incoming_funder(txns.get("transactions") or [], addr)
    if funding is not None:
        return {"address": addr, "found": True, "created_at_round": created_round, **funding}
    return {
        "address": addr,
        "found": False,
        "created_at_round": created_round,
        "error": "no incoming funding transaction found at the account's creation round",
    }


def _tx_direction_and_counterparty(
    t: dict[str, Any], addr: str
) -> tuple[str, str | None, int | None, int | None]:
    """(direction, counterparty, asset_id, raw_amount) for one transaction from `addr`'s point of view. Degrades gracefully for tx-types with no payment/asset-transfer inner object (app calls, key registration, etc.) — counterparty/asset_id/amount just come back None, the tx_type field alone still tells the caller what happened."""
    pay = t.get("payment-transaction") or {}
    axfer = t.get("asset-transfer-transaction") or {}
    receiver = axfer.get("receiver") or pay.get("receiver")
    amount = axfer.get("amount") if axfer else pay.get("amount")
    asset_id = axfer.get("asset-id") if axfer else None
    if t.get("sender") == addr:
        return "sent", receiver, asset_id, amount
    return "received", t.get("sender"), asset_id, amount


def _summarize_transaction(t: dict[str, Any], addr: str) -> dict[str, Any]:
    from datetime import UTC, datetime

    round_time = t.get("round-time")
    iso_time = (
        datetime.fromtimestamp(round_time, tz=UTC).isoformat()
        if isinstance(round_time, int | float)
        else None
    )
    direction, counterparty, asset_id, amount = _tx_direction_and_counterparty(t, addr)
    return {
        "txid": t.get("id"),
        "round": t.get("confirmed-round"),
        "round_time": iso_time,
        "tx_type": t.get("tx-type"),
        "direction": direction,
        "counterparty": counterparty,
        "asset_id": asset_id,
        "amount_raw": amount,
    }


def _tool_lookup_account_transactions(address: str, limit: int = 10) -> dict[str, Any]:
    """Recent transactions for an Algorand account, newest first, via the mainnet indexer — the general 'is this account still active, and doing what' signal chain_tools lacked. lookup_account gives a current balance snapshot; lookup_first_funding gives one specific historical moment (account creation); this gives the actual recent activity pattern — whether a claimed recurring process is still firing, whether an account has gone quiet, or what kind of transactions it's really doing, instead of guessing from a balance alone.

    amount_raw is unconverted (no decimals applied) — use get_asset_holder_share
    or lookup_asset's decimals field to convert an asset amount yourself if the
    story needs the real token count, not the raw integer.
    """
    addr = (address or "").strip()
    if not _is_valid_address(addr):
        return {"address": addr, "error": _INVALID_ADDRESS_ERROR}
    n = max(1, min(int(limit), 30))
    data = _mainnet_idx_get(f"/v2/accounts/{addr}/transactions", params={"limit": n})
    if not isinstance(data, dict):
        return {"error": "unexpected indexer response"}
    if data.get("error"):
        return data
    txns = [t for t in (data.get("transactions") or []) if isinstance(t, dict)]
    results = [_summarize_transaction(t, addr) for t in txns]
    return {
        "address": addr,
        "transaction_count_this_page": len(results),
        "most_recent_round_time": results[0]["round_time"] if results else None,
        "transactions": results,
    }


def _tool_get_asset_holder_share(asset_id: int | str, address: str) -> dict[str, Any]:
    """A specific address's share of an ASA's total supply, computed here (not left to the model) — use this instead of manually dividing lookup_asset's total by lookup_account's raw holding, which is exactly how a real fabricated "99.99%" concentration claim happened (2026-07-14): the model got the decimal-shift arithmetic wrong on a 15-digit raw amount."""
    addr = (address or "").strip()
    if not addr:
        return {"error": "address required"}
    asset = _tool_lookup_asset(asset_id)
    if asset.get("error"):
        return asset
    total = asset.get("total")
    decimals = asset.get("decimals")
    if not isinstance(total, int | float) or not total:
        return {"error": "asset has no usable total supply"}
    account = _tool_lookup_account(addr)
    if account.get("error"):
        return account
    target_asset_id = asset.get("asset_id")
    holding = next(
        (
            a.get("amount")
            for a in account.get("assets", [])
            if a.get("asset_id") == target_asset_id
        ),
        0,
    )
    holding = holding or 0
    holding_adjusted = (
        round(holding / (10**decimals), 6) if isinstance(decimals, int) and decimals >= 0 else None
    )
    return {
        "asset_id": asset.get("asset_id"),
        "address": addr,
        "holder_amount_adjusted": holding_adjusted,
        "total_supply_adjusted": asset.get("total_adjusted"),
        "share_pct": round(100 * holding / total, 4),
    }


def _tool_lookup_asset_holders(asset_id: int | str, limit: int = 10) -> dict[str, Any]:
    """Current holders of an ASA (balance > 0), via the mainnet indexer — the real 'is this collection actually held/traded' signal, the reverse of get_asset_holder_share (which needs a candidate address already in hand). For a 1/1 NFT (total=1), one call says whether the creator still holds it (never sold/distributed) or a different address does (a real transfer happened) — root-caused 2026-08-05: a compose treated a whole NFT collection as unverified/obscure after checking only DEX token-pool listings, which don't apply to 1/1 NFTs, instead of just checking whether any of the assets had actually moved off the creator's wallet.

    Capped at one indexer page (up to 100 raw balances, `limit` of those
    returned by size) — holder_count_is_complete tells you whether that page
    was the whole picture or there are more beyond it.
    """
    aid = str(asset_id).strip()
    if not aid.isdigit():
        return {"error": "asset_id must be numeric"}
    n = max(1, min(int(limit), 20))
    asset = _tool_lookup_asset(aid)
    if asset.get("error"):
        return asset
    creator = asset.get("creator")
    decimals = asset.get("decimals")
    data = _mainnet_idx_get(
        f"/v2/assets/{aid}/balances", params={"currency-greater-than": 0, "limit": 100}
    )
    if not isinstance(data, dict):
        return {"error": "unexpected indexer response"}
    if data.get("error"):
        return data
    balances = data.get("balances", []) or []

    def _adj(amount: int | float) -> int | float:
        if isinstance(decimals, int) and decimals >= 0:
            return round(amount / (10**decimals), 6)
        return amount

    balances = sorted(balances, key=lambda b: b.get("amount", 0), reverse=True)
    return {
        "asset_id": asset.get("asset_id"),
        "creator": creator,
        "holder_count_this_page": len(balances),
        "holder_count_is_complete": not data.get("next-token"),
        "creator_still_holds": any(b.get("address") == creator for b in balances),
        "top_holders": [
            {"address": b.get("address"), "amount_adjusted": _adj(b.get("amount", 0))}
            for b in balances[:n]
        ],
    }


def _tool_lookup_application(app_id: int | str) -> dict[str, Any]:
    """An application's (smart contract's) creator and DECODED global state — the on-chain variables a protocol exposes (e.g. governance proposal/vote tallies, admin addresses, parameters). Point it at a governance app id to verify what actually executed on-chain."""
    aid = str(app_id).strip()
    if not aid.isdigit():
        return {"error": "app_id must be a numeric application id"}
    data = _algod_get(f"/v2/applications/{aid}")
    if not isinstance(data, dict):
        return {"error": "unexpected algod response"}
    if data.get("error"):
        return data
    if data.get("_status") == 404:
        return {"app_id": int(aid), "error": "application not found"}
    p = data.get("params", {}) or {}
    gstate = p.get("global-state", []) or []
    decoded: dict[str, Any] = {}
    for kv in gstate:
        if not isinstance(kv, dict):
            continue
        try:
            decoded[_decode_key(kv.get("key", ""))] = _decode_value(kv.get("value", {}) or {})
        except Exception:
            continue
    return {
        "app_id": int(aid),
        "creator": p.get("creator"),
        "global_state_entries": len(gstate),
        "global_state": decoded,
    }


def _tool_get_consensus_stats() -> dict[str, Any]:
    """Algorand consensus participation from algod /v2/ledger/supply: ALGO stake currently ONLINE (securing the network) vs total stake, and the online share. The on-chain measure of participation scale. NOTE: this is online STAKE, not a node count — node count is off-chain telemetry the ledger does not expose."""
    data = _algod_get("/v2/ledger/supply")
    if not isinstance(data, dict):
        return {"error": "unexpected algod response"}
    if data.get("error"):
        return data
    online = data.get("online-money", data.get("online_money"))
    total = data.get("total-money", data.get("total_money"))
    if online is None or total is None:
        return {"error": "supply fields missing from algod response"}
    return {
        "round": data.get("current_round", data.get("current-round")),
        "online_stake_algo": round(online / 1e6, 6),
        "total_stake_algo": round(total / 1e6, 6),
        "online_pct": round(100 * online / total, 2) if total else None,
    }


def _testnet_idx_get(path: str, params: dict | None = None) -> dict[str, Any]:
    """GET a path off the public testnet INDEXER (history-capable, unlike algod).

    Returns parsed JSON, {"_status": 404} for a missing entity, or {"error": ...}.
    """
    import httpx

    from app.core.config import TESTNET_INDEXER_URL

    if not TESTNET_INDEXER_URL:
        return {"error": "testnet indexer not configured (TESTNET_INDEXER_URL unset)"}
    try:
        with httpx.Client(timeout=_TIMEOUT) as http:
            r = http.get(f"{TESTNET_INDEXER_URL}{path}", params=params)
        if r.status_code == 404:
            return {"_status": 404}
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _testnet_lookup_txid(txid: str) -> dict[str, Any]:
    data = _testnet_idx_get(f"/v2/transactions/{txid}")
    if not isinstance(data, dict) or data.get("error"):
        return data if isinstance(data, dict) else {"error": "unexpected indexer response"}
    if data.get("_status") == 404:
        return {"txid": txid, "found": False, "error": "transaction not found on testnet"}
    tx = data.get("transaction", {}) or {}
    return {
        "txid": txid,
        "found": True,
        "type": tx.get("tx-type"),
        "sender": tx.get("sender"),
        "confirmed_round": tx.get("confirmed-round"),
        "round_time": tx.get("round-time"),
        "created_application_index": tx.get("created-application-index"),
        "created_asset_index": tx.get("created-asset-index"),
        "fee": tx.get("fee"),
        "note_b64": tx.get("note"),
    }


def _testnet_lookup_app_id(app_id: str) -> dict[str, Any]:
    if not app_id.isdigit():
        return {"error": "app_id must be a numeric application id"}
    data = _testnet_idx_get(f"/v2/applications/{app_id}")
    if not isinstance(data, dict) or data.get("error"):
        return data if isinstance(data, dict) else {"error": "unexpected indexer response"}
    if data.get("_status") == 404:
        return {"app_id": int(app_id), "found": False, "error": "application not found on testnet"}
    app = data.get("application", {}) or {}
    return {
        "app_id": int(app_id),
        "found": True,
        "creator": (app.get("params", {}) or {}).get("creator"),
        "created_at_round": app.get("created-at-round"),
        "deleted": app.get("deleted", False),
        "deleted_at_round": app.get("deleted-at-round"),
    }


def _testnet_lookup_address(address: str) -> dict[str, Any]:
    if not _is_valid_address(address):
        return {"address": address, "error": _INVALID_ADDRESS_ERROR}
    acct = _testnet_idx_get(f"/v2/accounts/{address}")
    if not isinstance(acct, dict) or acct.get("error"):
        return acct if isinstance(acct, dict) else {"error": "unexpected indexer response"}
    if acct.get("_status") == 404:
        return {"address": address, "found": False, "error": "account not found on testnet"}
    a = acct.get("account", {}) or {}
    txns = _testnet_idx_get(f"/v2/accounts/{address}/transactions", params={"limit": 10})
    recent = []
    if isinstance(txns, dict) and not txns.get("error"):
        recent.extend(
            {
                "txid": t.get("id"),
                "type": t.get("tx-type"),
                "round": t.get("confirmed-round"),
                "round_time": t.get("round-time"),
            }
            for t in (txns.get("transactions", []) or [])[:10]
            if isinstance(t, dict)
        )
    return {
        "address": address,
        "found": True,
        "balance_algo": round((a.get("amount", 0) or 0) / 1e6, 6),
        "created_apps": a.get("total-created-apps"),
        "created_assets": a.get("total-created-assets"),
        "apps_opted_in": a.get("total-apps-opted-in"),
        "recent_transactions": recent,
    }


def _tool_testnet_lookup(
    txid: str = "",
    address: str = "",
    app_id: int | str = "",
) -> dict[str, Any]:
    """Verify Testnet on-chain activity via the public testnet indexer. Pass EXACTLY one of: a transaction id (confirm a tx happened and what it did), an account address (recent activity + what it created), or an application id (confirm a contract is deployed and when). Testnet only — for mainnet use lookup_* tools."""
    txid = (txid or "").strip()
    address = (address or "").strip()
    app_id = str(app_id).strip()

    if txid:
        return _testnet_lookup_txid(txid)
    if app_id:
        return _testnet_lookup_app_id(app_id)
    if address:
        return _testnet_lookup_address(address)
    return {"error": "pass one of txid, address, or app_id"}


CHAIN_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_account",
            "description": (
                "Live on-chain state of an Algorand account by address: ALGO balance, "
                "ASAs held, the apps it created or opted into, auth_addr, and sig_type. "
                "auth_addr is set only if this account was REKEYED — delegated its "
                "signing authority to a different address — and is real evidence of "
                "common control even when two accounts have different addresses: if "
                "two assets' creator addresses differ but both accounts share the SAME "
                "auth_addr, the same real signer controls both — do not conclude "
                "'different creator, so unrelated' without checking this first. "
                "sig_type tells you whether the account is a personal wallet (sig), a "
                "multisig with shared control (msig), or contract-controlled (lsig). "
                "Use to verify holdings, treasury balances, or whether an account "
                "participates in a protocol. The address MUST be one you actually found "
                "in a fetched page, search result, or another tool's output — never "
                "construct, guess, or pattern-match a plausible-looking one yourself "
                "(e.g. 'the project's name as a prefix'). If you don't have a real "
                "address for this project, say so in the article instead of inventing "
                "one to check."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "58-char Algorand address"}
                },
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_asset",
            "description": (
                "Algorand Standard Asset (ASA) parameters by asset id: name, total supply, "
                "decimals, creator, manager/freeze/clawback/reserve roles, and "
                "metadata_hash. Use to verify a token's real supply or who controls it. "
                "For an ARC-3/ARC-19 NFT, metadata_hash is the on-chain commitment its "
                "metadata JSON is supposed to match — if you fetch that metadata, you "
                "can actually check it against this instead of trusting it blindly."
            ),
            "parameters": {
                "type": "object",
                "properties": {"asset_id": {"type": "integer", "description": "numeric ASA id"}},
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_application",
            "description": (
                "An Algorand application (smart contract) by id: its creator and DECODED "
                "global state — the live on-chain variables it exposes (governance proposal/"
                "vote tallies, admin addresses, parameters). Point it at a governance app id "
                "to verify what executed on-chain rather than relying on a forum post."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_id": {"type": "integer", "description": "numeric application id"}
                },
                "required": ["app_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_asset_by_name",
            "description": (
                "Search mainnet Algorand Standard Assets by name or unit-name when "
                "you don't have the numeric asset_id yet (algod's lookup_asset needs "
                "an id and can't search by name). Each result already includes total "
                "supply (total_adjusted, decimal-corrected) alongside name/unit_name/"
                "creator — you do NOT need a separate lookup_asset call just to report "
                "a token's supply. Only call lookup_asset afterward if you need fields "
                "this doesn't return: manager/freeze/clawback/reserve roles, or the "
                "asset's metadata url."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "asset name or unit-name to search for, e.g. 'COMPX'",
                    },
                    "limit": {"type": "integer", "description": "1-20 results, default 5"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_first_funding",
            "description": (
                "Who first funded an Algorand account — the sender of the payment/"
                "asset-transfer confirmed in the account's own creation round, via "
                "the mainnet indexer. Use to check whether two accounts that look "
                "unrelated by address alone (different creators, different "
                "auth_addr) actually share a common funder — real evidence of a "
                "connection, not name-based guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "58-char Algorand address"}
                },
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_account_transactions",
            "description": (
                "Recent transactions for an Algorand account, newest first, via "
                "the mainnet indexer — use to check whether an account is "
                "actually still active and what it's doing, not just its current "
                "balance. lookup_account only gives a snapshot; this shows the "
                "real recent activity pattern — confirm a claimed recurring "
                "process is still firing, spot that an account has gone quiet, "
                "or see who it's actually transacting with. amount_raw is "
                "unconverted (no decimals applied)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "58-char Algorand address"},
                    "limit": {
                        "type": "integer",
                        "description": "1-30 most recent transactions to return, default 10",
                    },
                },
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_asset_holder_share",
            "description": (
                "A specific address's share of an ASA's total supply, as a real "
                "percentage computed here — use this instead of manually dividing "
                "lookup_asset's total by an amount from lookup_account when reporting "
                "a holder's concentration (e.g. 'the creator holds X% of supply'). "
                "Never compute that percentage yourself from the raw numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "integer", "description": "numeric ASA id"},
                    "address": {
                        "type": "string",
                        "description": "58-char Algorand address to check",
                    },
                },
                "required": ["asset_id", "address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_asset_holders",
            "description": (
                "Current holders of an ASA (balance > 0), via the indexer — checks "
                "whether a collection/token is actually held or traded, rather than "
                "just naming it. For a 1/1 NFT, this tells you in one call whether the "
                "creator STILL holds it (never sold/distributed) or someone else does "
                "(a real transfer happened) — do not call a collection 'unverified' or "
                "'no signs of trading' based only on DEX pool-listing tools, which "
                "don't apply to 1/1 NFTs at all. For a fungible token, use it to see "
                "real concentration (top holders) instead of assuming from supply alone."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "integer", "description": "numeric ASA id"},
                    "limit": {
                        "type": "integer",
                        "description": "1-20 top holders to return by balance, default 10",
                    },
                },
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_consensus_stats",
            "description": (
                "Algorand network consensus participation: ALGO stake currently ONLINE "
                "securing the network vs total stake, and the online %. The on-chain "
                "measure of participation/staking scale. NOTE: this is online STAKE, "
                "not a node count (node count is off-chain telemetry, not in the ledger)."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "testnet_lookup",
            "description": (
                "Verify a project's TESTNET on-chain activity via the public testnet "
                "indexer. Pass exactly ONE of: txid (confirm a transaction happened and "
                "what it created), address (recent activity + apps/assets it created), or "
                "app_id (confirm a smart contract is deployed and its creation round). Use "
                "to fact-check 'deployed on Testnet' claims. Testnet only — for mainnet "
                "use lookup_account/lookup_asset/lookup_application."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "txid": {"type": "string", "description": "transaction id to confirm"},
                    "address": {"type": "string", "description": "58-char account address"},
                    "app_id": {"type": "integer", "description": "numeric application id"},
                },
            },
        },
    },
]

CHAIN_HANDLERS: dict[str, Any] = {
    "lookup_account": _tool_lookup_account,
    "lookup_asset": _tool_lookup_asset,
    "lookup_asset_by_name": _tool_lookup_asset_by_name,
    "lookup_first_funding": _tool_lookup_first_funding,
    "lookup_account_transactions": _tool_lookup_account_transactions,
    "lookup_application": _tool_lookup_application,
    "get_asset_holder_share": _tool_get_asset_holder_share,
    "lookup_asset_holders": _tool_lookup_asset_holders,
    "get_consensus_stats": _tool_get_consensus_stats,
    "testnet_lookup": _tool_testnet_lookup,
}


def chain_tools() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """On-chain lookup tools (schemas, handlers). Always registered — the handlers degrade gracefully to {"error": ...} when ALGOD_URL is unset."""
    return list(CHAIN_SCHEMAS), dict(CHAIN_HANDLERS)
