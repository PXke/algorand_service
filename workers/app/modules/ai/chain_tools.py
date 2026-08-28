"""Read-only on-chain lookups backed by the wired algod node (ALGOD_URL/TOKEN, the same connector chain_reader.py uses). These answer the recurring "verify it on-chain" gap the writer kept working around — point lookup_application at a governance app to read its live proposal/vote state, etc.

algod gives CURRENT state only (no history — that needs an indexer). Every handler
is failure-tolerant: any error returns {"error": ...} and never aborts the article.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.redis_client import get_redis

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0


def _redis_client() -> redis.Redis:
    return get_redis()


def _cache_key(source: str, path: str, params: dict | None) -> str:
    """Deterministic Redis key for one (source, path, params) call -- params sorted so dict-insertion order never splits one logical call across two cache entries."""
    if not params:
        return f"chain:cache:{source}:{path}"
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return f"chain:cache:{source}:{path}?{qs}"


def _cache_get(key: str) -> dict[str, Any] | None:
    """Fetch and parse a cached JSON value, returning None on any failure (cache miss, Redis down, or corrupt entry) -- fail-soft, matches coingecko_cache.py."""
    import contextlib

    with contextlib.suppress(Exception):
        raw = _redis_client().get(key)
        if raw:
            return json.loads(raw)
    return None


def _cache_set(key: str, value: dict[str, Any], ttl: int) -> None:
    """Cache a JSON-serializable value under key for ttl seconds, failing soft."""
    import contextlib

    with contextlib.suppress(Exception):
        _redis_client().set(key, json.dumps(value), ex=ttl)


def _algod_get(path: str, *, cache_ttl: int = 0) -> dict[str, Any]:
    """GET a path off the operator-configured (trusted) algod node. Returns the parsed JSON, {"_status": 404} for a missing entity, or {"error": ...}.

    cache_ttl > 0 caches a genuine successful body in Redis for that many
    seconds -- never the {"error": ...} or {"_status": 404} shapes, so a
    transient failure or a not-yet-confirmed entity never poisons the cache.
    See CHAIN_CACHE_TTL_STATIC/SLOW/FAST in app.core.config for the tiers
    each caller picks from.
    """
    import httpx

    from app.core.config import ALGOD_TOKEN, ALGOD_URL

    if not ALGOD_URL:
        return {"error": "algod not configured (ALGOD_URL unset)"}
    key = _cache_key("algod", path, None) if cache_ttl > 0 else ""
    if key:
        cached = _cache_get(key)
        if cached is not None:
            return cached
    headers = {"X-Algo-API-Token": ALGOD_TOKEN} if ALGOD_TOKEN else {}
    try:
        with httpx.Client(timeout=_TIMEOUT) as http:
            r = http.get(f"{ALGOD_URL}{path}", headers=headers)
        if r.status_code == 404:
            return {"_status": 404}
        r.raise_for_status()
        result = r.json()
        if key and isinstance(result, dict) and not result.get("error"):
            _cache_set(key, result, cache_ttl)
        return result
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


_LOOKUP_ACCOUNT_PAGE = 25


def _tool_lookup_account(address: str, created_assets_offset: int = 0) -> dict[str, Any]:
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

    created_assets_offset (added 2026-08-10, self-reported gap): algod's
    response already carries the FULL created-assets list -- total_created_
    assets was always accurate -- but the returned id list silently capped
    at 25 with no way to see the rest, so a prolific creator's exact roster
    (e.g. confirming a 310-asset NFT series' full id range) was unreachable.
    Re-call with a higher offset to page through the rest; the other three
    lists (held assets, created/opted-in apps) keep the same one-page cap
    since no investigation has hit their limit yet -- add paging there too
    if one does.
    """
    addr = (address or "").strip()
    if not addr:
        return {"error": "address required"}
    if not _is_valid_address(addr):
        return {"address": addr, "error": _INVALID_ADDRESS_ERROR}
    from app.core.config import CHAIN_CACHE_TTL_FAST

    data = _algod_get(f"/v2/accounts/{addr}", cache_ttl=CHAIN_CACHE_TTL_FAST)
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
    offset = max(0, created_assets_offset)
    page = created_assets[offset : offset + _LOOKUP_ACCOUNT_PAGE]
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
        "created_assets": [a.get("index") for a in page if isinstance(a, dict)],
        "created_assets_offset": offset,
        "created_assets_has_more": offset + _LOOKUP_ACCOUNT_PAGE < len(created_assets),
        "created_apps": [a.get("id") for a in created_apps[:25] if isinstance(a, dict)],
        "opted_in_apps": [a.get("id") for a in opted_in_apps[:25] if isinstance(a, dict)],
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
    from app.core.config import CHAIN_CACHE_TTL_STATIC

    data = _algod_get(f"/v2/assets/{aid}", cache_ttl=CHAIN_CACHE_TTL_STATIC)
    if not isinstance(data, dict):
        return {"error": "unexpected algod response"}
    if data.get("error"):
        return data
    if data.get("_status") == 404:
        return {"asset_id": int(aid), "error": "asset not found"}
    p = data.get("params", {}) or {}
    total, decimals, total_adjusted = _asset_total_adjusted(p)
    result: dict[str, Any] = {
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
    # Root-caused 2026-08-21 (HesabPay/HAFN, twice in one night): some ASAs
    # register `total` at the literal uint64 max as a "no fixed cap"
    # sentinel, not a real economic supply. get_asset_holder_share now
    # refuses to compute a percentage against it -- but this field is also
    # exposed here and combined manually with lookup_asset_holders'
    # creator_holding_adjusted, which reproduced the exact same wrong
    # ~100%-of-supply conclusion after the first fix, in the writer's own
    # reasoning trace. Flagging it at the source (every caller of
    # lookup_asset sees this total) closes the gap for every tool built on
    # top of it, not just the one that already got patched once.
    if total == _ASA_UINT64_MAX_TOTAL:
        result["total_is_no_cap_sentinel"] = True
        result["total_supply_warning"] = (
            "total/total_adjusted is the uint64-max 'no fixed cap' sentinel, not "
            "a real economic supply -- do not divide any balance by it or report "
            "a resulting percentage as this asset's holder concentration."
        )
    return result


def _mainnet_idx_get(
    path: str, params: dict | None = None, *, cache_ttl: int = 0
) -> dict[str, Any]:
    """GET a path off the public MAINNET indexer (name-search capable, unlike algod). Returns parsed JSON, {"_status": 404} for a missing entity, or {"error": ...}.

    cache_ttl > 0 caches a genuine successful body -- see _algod_get's
    docstring for the caching contract, identical here.
    """
    import httpx

    from app.core.config import MAINNET_INDEXER_URL

    if not MAINNET_INDEXER_URL:
        return {"error": "mainnet indexer not configured (MAINNET_INDEXER_URL unset)"}
    key = _cache_key("mainnet_idx", path, params) if cache_ttl > 0 else ""
    if key:
        cached = _cache_get(key)
        if cached is not None:
            return cached
    try:
        with httpx.Client(timeout=_TIMEOUT) as http:
            r = http.get(f"{MAINNET_INDEXER_URL}{path}", params=params)
        if r.status_code == 404:
            return {"_status": 404}
        r.raise_for_status()
        result = r.json()
        if key and isinstance(result, dict) and not result.get("error"):
            _cache_set(key, result, cache_ttl)
        return result
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
    from app.core.config import CHAIN_CACHE_TTL_SLOW

    data = _mainnet_idx_get(
        "/v2/assets", params={"unit": q, "limit": 100}, cache_ttl=CHAIN_CACHE_TTL_SLOW
    )
    if not isinstance(data, dict):
        return {"error": "unexpected indexer response"}
    if data.get("error"):
        return data
    assets = data.get("assets", []) or []
    if not assets:
        data = _mainnet_idx_get(
            "/v2/assets", params={"name": q, "limit": n}, cache_ttl=CHAIN_CACHE_TTL_SLOW
        )
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
    from app.core.config import CHAIN_CACHE_TTL_SLOW, CHAIN_CACHE_TTL_STATIC

    acct = _mainnet_idx_get(f"/v2/accounts/{addr}", cache_ttl=CHAIN_CACHE_TTL_SLOW)
    if not isinstance(acct, dict):
        return {"error": "unexpected indexer response"}
    if acct.get("error"):
        return acct
    if acct.get("_status") == 404:
        return {"address": addr, "found": False, "error": "account not found on mainnet"}
    created_round = (acct.get("account") or {}).get("created-at-round")
    if created_round is None:
        return {"address": addr, "found": False, "error": "no created-at-round available"}
    # A fixed historical round range -- once fetched, this result is permanent.
    txns = _mainnet_idx_get(
        f"/v2/accounts/{addr}/transactions",
        params={"min-round": created_round, "max-round": created_round, "limit": 20},
        cache_ttl=CHAIN_CACHE_TTL_STATIC,
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
    direction, counterparty, asset_id, amount = _tx_direction_and_counterparty(t, addr)
    return {
        "txid": t.get("id"),
        "round": t.get("confirmed-round"),
        "round_time": _iso_round_time(t),
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
    from app.core.config import CHAIN_CACHE_TTL_FAST

    data = _mainnet_idx_get(
        f"/v2/accounts/{addr}/transactions", params={"limit": n}, cache_ttl=CHAIN_CACHE_TTL_FAST
    )
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


def _tool_lookup_transaction_note(txid: str) -> dict[str, Any]:
    """The note field of one specific transaction, by its txid, via the mainnet indexer.

    A transaction's note is where a project puts a memo — an on-chain
    "message", a payment reference, a governance vote's rationale text. No
    existing tool exposes it: lookup_account_transactions/lookup_asset_transactions
    summarize a LIST of transactions and never surface note (mostly empty,
    would bloat every list). Use this once a specific txid's note actually
    matters to a claim, instead of guessing what a memo says from a
    third-party site's paraphrase of it.

    The indexer returns note as base64 bytes; most real memos are UTF-8 text,
    decoded here automatically. A note that isn't valid UTF-8 (binary data,
    an encrypted payload) comes back as base64 with is_utf8_text=false, so a
    real memo is never confused with base64 noise.
    """
    tid = (txid or "").strip()
    if not tid:
        return {"error": "txid required"}
    from app.core.config import CHAIN_CACHE_TTL_STATIC

    data = _mainnet_idx_get(f"/v2/transactions/{tid}", cache_ttl=CHAIN_CACHE_TTL_STATIC)
    if not isinstance(data, dict):
        return {"error": "unexpected indexer response"}
    if data.get("_status") == 404:
        return {"txid": tid, "error": "transaction not found"}
    if data.get("error"):
        return data
    t = data.get("transaction") or {}
    note_b64 = t.get("note")
    if not note_b64:
        return {
            "txid": tid,
            "round": t.get("confirmed-round"),
            "round_time": _iso_round_time(t),
            "has_note": False,
            "note": None,
        }
    try:
        note_bytes = base64.b64decode(note_b64)
    except Exception:
        return {"txid": tid, "has_note": True, "note_base64": note_b64, "error": "malformed base64"}
    try:
        note_text = note_bytes.decode("utf-8")
        is_utf8_text = True
    except UnicodeDecodeError:
        note_text = None
        is_utf8_text = False
    return {
        "txid": tid,
        "round": t.get("confirmed-round"),
        "round_time": _iso_round_time(t),
        "has_note": True,
        "is_utf8_text": is_utf8_text,
        "note": note_text,
        "note_base64": note_b64 if not is_utf8_text else None,
        "note_byte_length": len(note_bytes),
    }


def _tool_lookup_arc69_metadata(asset_id: int | str) -> dict[str, Any]:
    """An ASA's ARC-69 attributes (traits, ratings, any structured properties an issuer wrote in), read from the right place.

    Root-caused live 2026-08-11 (Lumi Rogue "gungiELO" incident): the writer
    called lookup_asset and read its `url` field, got a bit.ly link that
    resolved to a plain PNG, and reported the NFT's on-chain rating as
    unverifiable. That was the wrong field for the wrong reason: ARC-69
    (unlike ARC-19/ARC-3) does NOT put structured attributes behind the
    asset's url — url is just the artwork. ARC-69 attributes live in the
    NOTE field of the asset's most recent asset-config (acfg) transaction,
    as base64-encoded JSON with a "properties" object. This tool reads that
    field directly instead of making the writer improvise with fetch_url on
    the wrong target — exactly the tool the writer's own trace asked for via
    suggest_tool immediately after making this mistake.

    Returns the parsed JSON on success. A same-collection NFT can have
    per-token distinct metadata (each mint's own acfg note) and a manager
    can rewrite it later (multiple acfg transactions) — this always reads
    the MOST RECENT one, since that's the attribute state a marketplace or
    reader would see today.
    """
    try:
        aid = int(asset_id)
    except (TypeError, ValueError):
        return {"error": "asset_id must be numeric"}
    from app.core.config import CHAIN_CACHE_TTL_SLOW

    # SLOW, not STATIC: a manager CAN rewrite this via a later acfg (see
    # docstring) -- unlike a plain transaction note, "most recent config" is
    # not permanent.
    data = _mainnet_idx_get(
        f"/v2/assets/{aid}/transactions",
        params={"tx-type": "acfg", "limit": 10},
        cache_ttl=CHAIN_CACHE_TTL_SLOW,
    )
    if not isinstance(data, dict):
        return {"error": "unexpected indexer response"}
    if data.get("_status") == 404 or not data.get("transactions"):
        return {"asset_id": aid, "error": "no asset-config transactions found for this asset"}
    if data.get("error"):
        return data
    txns = [t for t in data["transactions"] if isinstance(t, dict)]
    if not txns:
        return {"asset_id": aid, "error": "no asset-config transactions found for this asset"}
    # Indexer order for this endpoint is not guaranteed newest-first -- sort
    # explicitly so a manager's later metadata update always wins over the
    # original mint's.
    latest = max(txns, key=lambda t: t.get("confirmed-round") or 0)
    note_b64 = latest.get("note")
    if not note_b64:
        return {
            "asset_id": aid,
            "round": latest.get("confirmed-round"),
            "has_metadata": False,
            "note": "most recent asset-config transaction carries no note field",
        }
    try:
        note_bytes = base64.b64decode(note_b64)
        parsed = json.loads(note_bytes)
    except Exception:
        return {
            "asset_id": aid,
            "round": latest.get("confirmed-round"),
            "has_metadata": False,
            "note": "note field is not valid JSON -- not ARC-69 formatted",
        }
    return {
        "asset_id": aid,
        "round": latest.get("confirmed-round"),
        "round_time": _iso_round_time(latest),
        "has_metadata": True,
        "standard": parsed.get("standard"),
        "metadata": parsed,
    }


def _iso_round_time(t: dict[str, Any]) -> str | None:
    from datetime import UTC, datetime

    round_time = t.get("round-time")
    return (
        datetime.fromtimestamp(round_time, tz=UTC).isoformat()
        if isinstance(round_time, int | float)
        else None
    )


def _summarize_asset_transaction(t: dict[str, Any]) -> dict[str, Any]:
    axfer = t.get("asset-transfer-transaction") or {}
    amount = axfer.get("amount") or 0
    close_amount = axfer.get("close-amount") or 0
    # A zero-amount axfer with no close-to is routine opt-in/opt-out
    # housekeeping (the standard way to declare intent to hold an asset on
    # Algorand), not a real transfer of ownership — root-caused 2026-08-06:
    # an asset's most RECENT transaction by timestamp was exactly this kind
    # of noise (an unrelated account opting out with nothing to redirect),
    # while the actual last real transfer was 3+ years earlier. Flagging it
    # explicitly instead of trusting round_time recency alone.
    is_real_transfer = amount > 0 or close_amount > 0
    return {
        "txid": t.get("id"),
        "round": t.get("confirmed-round"),
        "round_time": _iso_round_time(t),
        "tx_type": t.get("tx-type"),
        "sender": t.get("sender"),
        "receiver": axfer.get("receiver") or axfer.get("close-to"),
        "amount_raw": amount,
        "is_real_transfer": is_real_transfer,
    }


def _tool_lookup_asset_transactions(asset_id: int | str, limit: int = 10) -> dict[str, Any]:
    """Recent transaction/transfer history for a specific ASA, via the mainnet indexer — marketplace-agnostic, on-chain-native event history for one asset (creation, every transfer, to/from whom), unlike lookup_asset_holders which only gives a current snapshot. Use to check how much something has actually traded, independent of any one marketplace's own listing page.

    is_real_transfer distinguishes an actual ownership-moving transfer
    (amount or close-amount > 0) from routine zero-amount opt-in/opt-out
    housekeeping, which is common and NOT evidence of recent trading —
    most_recent_real_transfer_round_time is the signal to trust for "when
    did this last actually change hands", not the newest entry by itself.

    The indexer returns an asset's transactions oldest-first with no
    server-side reverse-sort option (confirmed 2026-08-06 — unlike account
    transactions, which ARE newest-first by default). For a HIGH-VOLUME
    asset (way more transactions in its lifetime than one page can hold),
    an unbounded page is anchored at the asset's CREATION, not "now" — a
    real incident (2026-08-06): a stablecoin with millions of lifetime
    transfers came back "no real transfers since 2022" from this method
    alone, while the SAME asset had genuine transfers minutes old (found
    only via lookup_account_transactions on a counterparty). Fixed by
    checking a recent round-window (via algod's current round) FIRST, and
    only falling back to the from-genesis oldest page — now explicitly
    labeled as such via is_recent_window — when the recent window (and one
    widened retry) both come back empty.
    """
    aid = str(asset_id).strip()
    if not aid.isdigit():
        return {"error": "asset_id must be numeric"}
    n = max(1, min(int(limit), 30))
    page_size = 1000

    from app.core.config import CHAIN_CACHE_TTL_FAST, CHAIN_CACHE_TTL_STATIC

    status = _algod_get("/v2/status", cache_ttl=CHAIN_CACHE_TTL_FAST)
    current_round = status.get("last-round") if isinstance(status, dict) else None

    recent_window_rounds: int | None = None
    recent_txns: list[dict[str, Any]] = []
    recent_next_token: str | None = None
    if isinstance(current_round, int):
        # Start NARROW: a genuinely high-volume asset (e.g. a widely-used
        # stablecoin) can produce more than the 1000-row page cap within
        # even a couple of DAYS (~2 tx/round observed live on HAFN,
        # 2026-08-06) — a wide first window silently lands back on stale
        # data, the same trap this fix exists to avoid. Only widen if a
        # narrower window comes back genuinely empty. Rough round counts at
        # Algorand's ~2.8s block time: 300 ≈ 14 min, 20,000 ≈ 16 hr,
        # 500,000 ≈ 16 days.
        for window in (300, 20_000, 500_000):
            min_round = max(0, current_round - window)
            data = _mainnet_idx_get(
                f"/v2/assets/{aid}/transactions",
                params={"limit": page_size, "min-round": min_round},
                cache_ttl=CHAIN_CACHE_TTL_FAST,
            )
            if not isinstance(data, dict) or data.get("error"):
                break  # indexer trouble — fall through to the genesis page below
            txns = [t for t in (data.get("transactions") or []) if isinstance(t, dict)]
            if txns:
                recent_window_rounds = window
                recent_txns = txns
                recent_next_token = data.get("next-token")
                break

    if recent_txns:
        tail = recent_txns[-n:]
        results = [_summarize_asset_transaction(t) for t in tail]
        real_transfers = [r for r in results if r["is_real_transfer"]]
        return {
            "asset_id": int(aid),
            "is_recent_window": True,
            "recent_window_rounds_checked": recent_window_rounds,
            "recent_window_truncated": len(recent_txns) >= page_size and bool(recent_next_token),
            "most_recent_real_transfer_round_time": (
                real_transfers[-1]["round_time"] if real_transfers else None
            ),
            "most_recent_activity_round_time": results[-1]["round_time"],
            "transaction_count_this_page": len(recent_txns),
            "transactions": results,
        }

    # No recent-window data available (algod unconfigured, indexer error, or
    # the asset genuinely had nothing in the last ~month) — fall back to the
    # oldest page from genesis. For a high-volume asset this reflects EARLY
    # history, not "most recent" — is_recent_window: False marks that. This
    # page is definitionally historical (oldest-first, no min-round), so it's
    # cached STATIC unlike the recent-window queries above.
    data = _mainnet_idx_get(
        f"/v2/assets/{aid}/transactions",
        params={"limit": page_size},
        cache_ttl=CHAIN_CACHE_TTL_STATIC,
    )
    if not isinstance(data, dict):
        return {"error": "unexpected indexer response"}
    if data.get("error"):
        return data
    txns = [t for t in (data.get("transactions") or []) if isinstance(t, dict)]
    tail = txns[-n:]
    results = [_summarize_asset_transaction(t) for t in tail]
    real_transfers = [r for r in results if r["is_real_transfer"]]
    return {
        "asset_id": int(aid),
        "is_recent_window": False,
        "most_recent_real_transfer_round_time": (
            real_transfers[-1]["round_time"] if real_transfers else None
        ),
        "transaction_count_this_page": len(txns),
        "page_may_be_incomplete": len(txns) >= page_size and bool(data.get("next-token")),
        "most_recent_activity_round_time": results[-1]["round_time"] if results else None,
        "transactions": results,
    }


_ASA_VOLUME_PAGE = 1000


def _tool_get_asset_transaction_volume(
    asset_id: int | str, min_round: int = 0, max_pages: int = 25
) -> dict[str, Any]:
    """Aggregate transaction count + total amount moved for an ASA since a given round (default: since creation), via the mainnet indexer.

    lookup_asset_transactions only returns ONE page (<=1000 rows) of an
    asset's history -- no good for checking a headline claim like "3.5M
    transactions" or "$10B in volume" (self-reported gap, 2026-08-10:
    HAFN's "3.5M+ transactions" / "$10B in 2024 volume" claims could only
    be attributed to the Foundation, not checked, because the indexer has
    no aggregate-totals endpoint of its own). This pages through up to
    max_pages * 1000 transactions and sums them -- for a low-volume asset
    that IS the true lifetime total (complete: true); for a high-volume one
    it hits the page cap first, in which case complete is false and the
    counts are an honest LOWER BOUND -- still useful, since "at least
    25,000 real transfers in this window" can already falsify an inflated
    claim even without going fully exhaustive. amount is in RAW base
    units -- combine with lookup_asset's decimals to adjust it, and with
    round_to_date to scope min_round to a specific calendar date.

    Root-caused 2026-08-11 (self-reported false complete:true on
    meld.gold): completeness used to be inferred from a short page
    (fewer rows than requested) as well as a missing next-token, but the
    indexer can return a short page that still carries a next-token when
    server-side filtering trims a batch after fetching it. The only safe
    "no more data" signal is a missing next-token -- a short page alone
    no longer marks complete.
    """
    aid = str(asset_id).strip()
    if not aid.isdigit():
        return {"error": "asset_id must be numeric"}
    pages_cap = max(1, min(int(max_pages), 100))
    floor_round = max(0, min_round)

    from app.core.config import CHAIN_CACHE_TTL_SLOW

    total_count = 0
    real_transfer_count = 0
    total_amount_raw = 0
    earliest_round: int | None = None
    latest_round: int | None = None
    next_token: str | None = None
    pages_fetched = 0
    complete = False

    while pages_fetched < pages_cap:
        params: dict[str, Any] = {"limit": _ASA_VOLUME_PAGE, "min-round": floor_round}
        if next_token:
            params["next"] = next_token
        data = _mainnet_idx_get(
            f"/v2/assets/{aid}/transactions", params=params, cache_ttl=CHAIN_CACHE_TTL_SLOW
        )
        if not isinstance(data, dict) or data.get("error"):
            if pages_fetched == 0:
                return data if isinstance(data, dict) else {"error": "unexpected indexer response"}
            break  # keep the partial aggregate already gathered rather than discarding it
        pages_fetched += 1
        txns = [t for t in (data.get("transactions") or []) if isinstance(t, dict)]
        for t in txns:
            summary = _summarize_asset_transaction(t)
            total_count += 1
            if summary["is_real_transfer"]:
                real_transfer_count += 1
                total_amount_raw += summary["amount_raw"]
            rnd = summary["round"]
            if rnd is not None:
                earliest_round = rnd if earliest_round is None else min(earliest_round, rnd)
                latest_round = rnd if latest_round is None else max(latest_round, rnd)
        next_token = data.get("next-token")
        if not next_token:
            complete = True
            break

    return {
        "asset_id": int(aid),
        "min_round_checked": floor_round,
        "transaction_count": total_count,
        "real_transfer_count": real_transfer_count,
        "total_amount_moved_raw": total_amount_raw,
        "earliest_round_seen": earliest_round,
        "latest_round_seen": latest_round,
        "pages_fetched": pages_fetched,
        "complete": complete,
        "note": (
            "exhaustive -- this IS the true total since min_round_checked"
            if complete
            else f"hit the {pages_cap}-page cap; this is a LOWER BOUND, not the true total"
        ),
    }


_COLLECTION_TIMELINE_DEFAULT_SAMPLE = 40
_COLLECTION_TIMELINE_MAX_SAMPLE = 100


def _tool_nft_collection_distribution_timeline(
    creator_address: str,
    max_assets: int = _COLLECTION_TIMELINE_DEFAULT_SAMPLE,
    asset_ids: list[int] | None = None,
) -> dict[str, Any]:
    """WHEN each item in an NFT collection was first sent out by its creator -- a real "is adoption rising, flat, or stalled since mint" trend, reconstructed from on-chain transfer history.

    Deliberately does NOT return a price. Self-reported gap (2026-08-14,
    LumiRogue Ankh benchmark): three separate compose sessions asked for an
    Algorand NFT collection's floor-price/volume HISTORY. Investigated live
    2026-08-15 before building anything: the obvious design (pair each real
    transfer with a co-grouped Payment transaction to derive a sale price)
    does NOT hold for this collection -- every sampled Ankh's first transfer
    is an UNGROUPED direct creator-to-buyer send with no payment transaction
    anywhere nearby (checked group field, checked payments to the creator
    account in the same round window, checked for a marketplace app-call
    referencing the asset -- all empty). The buyer very likely pays through
    an off-chain channel (this project is Base44-hosted) and the creator
    hands over the NFT separately once that clears -- there is no ALGO price
    to recover from chain data for THIS distribution mechanism. What IS
    real and verifiable from the exact same data: exactly when each item
    left the creator's hands, which is still genuine adoption-trend signal
    without inventing a price that was never on-chain to begin with.

    A secondary-marketplace RESALE (a later transfer where the sender is NOT
    the creator) would be a different, potentially price-bearing event, but
    none were observed in this investigation's samples -- flagged separately
    per asset (resold: true/false) rather than assumed.

    max_assets bounds real cost: a large collection (this one has 1,000
    items) can't be walked exhaustively in one call, since each asset needs
    its own transaction-history fetch. Defaults to a moderate sample from
    the creator's created-assets list (oldest-first, i.e. the order they
    were minted); pass asset_ids explicitly to check specific items instead
    of a sample. sampled/total_created_assets makes clear this is a sample,
    not a census, exactly like get_asset_transaction_volume's complete flag.
    """
    addr = (creator_address or "").strip()
    if not addr:
        return {"error": "creator_address required"}
    if not _is_valid_address(addr):
        return {"address": addr, "error": _INVALID_ADDRESS_ERROR}

    from app.core.config import CHAIN_CACHE_TTL_FAST, CHAIN_CACHE_TTL_SLOW

    if asset_ids:
        sample_ids = [int(a) for a in asset_ids][:_COLLECTION_TIMELINE_MAX_SAMPLE]
        total_created = len(sample_ids)
    else:
        acct = _algod_get(f"/v2/accounts/{addr}", cache_ttl=CHAIN_CACHE_TTL_FAST)
        if not isinstance(acct, dict):
            return {"error": "unexpected algod response"}
        if acct.get("error"):
            return acct
        if acct.get("_status") == 404:
            return {"address": addr, "error": "account not found"}
        created = [a for a in (acct.get("created-assets") or []) if isinstance(a, dict)]
        total_created = len(created)
        cap = max(1, min(int(max_assets), _COLLECTION_TIMELINE_MAX_SAMPLE))
        sample_ids = [a.get("index") for a in created[:cap] if a.get("index") is not None]

    items: list[dict[str, Any]] = []
    for aid in sample_ids:
        data = _mainnet_idx_get(
            f"/v2/assets/{aid}/transactions",
            params={"limit": _ASA_VOLUME_PAGE},
            cache_ttl=CHAIN_CACHE_TTL_SLOW,
        )
        if not isinstance(data, dict) or data.get("error"):
            items.append({"asset_id": aid, "claimed": False, "error": "lookup failed"})
            continue
        real_transfers = [
            s
            for s in (_summarize_asset_transaction(t) for t in (data.get("transactions") or []))
            if s["is_real_transfer"]
        ]
        real_transfers.sort(key=lambda s: (s["round"] is None, s["round"]))
        first_from_creator = next((s for s in real_transfers if s["sender"] == addr), None)
        if first_from_creator is None:
            items.append({"asset_id": aid, "claimed": False})
            continue
        later_resale = any(
            s["round"] is not None
            and first_from_creator["round"] is not None
            and s["round"] > first_from_creator["round"]
            and s["sender"] != addr
            for s in real_transfers
        )
        items.append(
            {
                "asset_id": aid,
                "claimed": True,
                "claimed_at": first_from_creator["round_time"],
                "claimed_at_round": first_from_creator["round"],
                "claimed_by": first_from_creator["receiver"],
                "resold_since": later_resale,
            }
        )

    claimed = [i for i in items if i.get("claimed")]
    claimed_dates = sorted(i["claimed_at"] for i in claimed if i.get("claimed_at"))
    return {
        "creator_address": addr,
        "total_created_assets": total_created,
        "sampled": len(sample_ids),
        "note": (
            f"sampled {len(sample_ids)} of {total_created} created assets -- "
            "a TREND from this sample, not a census of the whole collection"
            if len(sample_ids) < total_created
            else "every created asset was checked"
        ),
        "claimed_count": len(claimed),
        "unclaimed_count": len(items) - len(claimed),
        "resold_count": sum(1 for i in claimed if i.get("resold_since")),
        "earliest_claim_at": claimed_dates[0] if claimed_dates else None,
        "most_recent_claim_at": claimed_dates[-1] if claimed_dates else None,
        "items": items,
    }


_ASA_UINT64_MAX_TOTAL = 18446744073709551615  # 2**64 - 1


def _tool_get_asset_holder_share(asset_id: int | str, address: str) -> dict[str, Any]:
    """A specific address's share of an ASA's total supply, computed here (not left to the model) — use this instead of manually dividing lookup_asset's total by lookup_account's raw holding, which is exactly how a real fabricated "99.99%" concentration claim happened (2026-07-14): the model got the decimal-shift arithmetic wrong on a 15-digit raw amount.

    Root-caused 2026-08-11 (self-reported: lookup_asset_holders showed an
    address holding 1.9M STEAK, this tool reported 0.0 share for the same
    address/asset pair): used to read the holding off lookup_account's
    `assets` list, which -- like created_assets before its 2026-08-10 fix --
    is silently capped at 25 entries. An address opted into more than 25
    different ASAs would show 0 for any holding past the cap. Fixed by
    querying algod's per-asset holding endpoint directly instead, which has
    no such cap.

    Root-caused again 2026-08-21 (HesabPay/HAFN): some ASAs register their
    `total` at the literal uint64 max as a "no fixed cap" sentinel, not a
    real economic supply figure. A reserve/treasury account holding most of
    that registered max then divides out to ~100% -- technically correct
    arithmetic, but the writer read it as "the issuer holds virtually all of
    HAFN, non-issuer balances are modest" while lookup_asset_holders was
    showing real, live balances spread across tens of thousands of distinct
    accounts in the same session. The article shipped the wrong claim
    despite the model's own trace flagging the contradiction ("that's
    contradictory") a few lines earlier -- it trusted the clean percentage
    over the messier real evidence. Refusing outright for this specific,
    well-documented sentinel value is safer than trying to guess a "real"
    total.
    """
    addr = (address or "").strip()
    if not addr:
        return {"error": "address required"}
    if not _is_valid_address(addr):
        return {"address": addr, "error": _INVALID_ADDRESS_ERROR}
    asset = _tool_lookup_asset(asset_id)
    if asset.get("error"):
        return asset
    total = asset.get("total")
    decimals = asset.get("decimals")
    if not isinstance(total, int | float) or not total:
        return {"error": "asset has no usable total supply"}
    if total == _ASA_UINT64_MAX_TOTAL:
        return {
            "asset_id": asset.get("asset_id"),
            "address": addr,
            "error": (
                "this asset's registered total supply is the uint64-max 'no fixed "
                "cap' sentinel, not a real economic total -- any percentage "
                "computed against it is meaningless (a reserve account holding "
                "billions of raw units can read as ~100% of a cap nothing was ever "
                "meant to fully mint). Do not report a holder-share percentage for "
                "this asset. Use lookup_asset_holders for real distribution instead."
            ),
        }
    target_asset_id = asset.get("asset_id")
    from app.core.config import CHAIN_CACHE_TTL_SLOW

    holding_data = _algod_get(
        f"/v2/accounts/{addr}/assets/{target_asset_id}", cache_ttl=CHAIN_CACHE_TTL_SLOW
    )
    if holding_data.get("_status") == 404:
        holding = 0
    elif holding_data.get("error"):
        return holding_data
    else:
        holding = holding_data.get("asset-holding", {}).get("amount") or 0
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

    creator_still_holds is checked via a DIRECT lookup of the creator's own
    account, not by searching for them in the holders page below — root-
    caused 2026-08-06 against a real fungible token where the creator held
    ~99.99999% of supply: the holders page is capped at 100 raw balances in
    indexer-default order, NOT sorted by amount, so a creator with a
    100+-holder token can easily be absent from that page despite holding
    virtually everything, producing a false "creator no longer holds it".
    top_holders (best-of-page-100, amount-sorted) is still useful for who
    else holds it, just not authoritative for the creator specifically.
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
    asset_id_int = asset.get("asset_id")

    def _adj(amount: int | float) -> int | float:
        if isinstance(decimals, int) and decimals >= 0:
            return round(amount / (10**decimals), 6)
        return amount

    creator_account = (
        _tool_lookup_account(creator) if creator else {"error": "no creator on record"}
    )
    creator_lookup_error = creator_account.get("error")
    creator_holding = (
        0
        if creator_lookup_error
        else next(
            (
                a.get("amount")
                for a in (creator_account.get("assets") or [])
                if a.get("asset_id") == asset_id_int
            ),
            0,
        )
    )

    from app.core.config import CHAIN_CACHE_TTL_SLOW

    data = _mainnet_idx_get(
        f"/v2/assets/{aid}/balances",
        params={"currency-greater-than": 0, "limit": 100},
        cache_ttl=CHAIN_CACHE_TTL_SLOW,
    )
    if not isinstance(data, dict):
        return {"error": "unexpected indexer response"}
    if data.get("error"):
        return data
    balances = sorted(
        data.get("balances", []) or [], key=lambda b: b.get("amount", 0), reverse=True
    )
    return {
        "asset_id": asset_id_int,
        "creator": creator,
        "creator_still_holds": bool(creator_holding),
        "creator_holding_adjusted": _adj(creator_holding) if creator_holding else 0,
        **({"creator_lookup_error": creator_lookup_error} if creator_lookup_error else {}),
        # Propagated from lookup_asset (see its own comment): creator_holding
        # is a raw balance, not a share -- dividing it by a separately-fetched
        # total_adjusted reproduces the exact wrong-concentration conclusion
        # get_asset_holder_share now refuses to make, just computed by hand
        # instead. Surface the same warning here since this is the other tool
        # that hands out the raw ingredient for that computation.
        **(
            {"total_supply_warning": asset["total_supply_warning"]}
            if asset.get("total_is_no_cap_sentinel")
            else {}
        ),
        "holder_count_this_page": len(balances),
        "holder_count_is_complete": not data.get("next-token"),
        "top_holders": [
            {"address": b.get("address"), "amount_adjusted": _adj(b.get("amount", 0))}
            for b in balances[:n]
        ],
    }


def _tool_trace_asset_creator(asset_id: int | str) -> dict[str, Any]:
    """Resolve an ASA's real-world creator identity -- who actually made it, not just what its name string says.

    Root-caused 2026-08-11 (Lumi Rogue LUMI-token incident): both Mistral
    and DeepSeek independently called lookup_asset_by_name("LUMI"), took
    the top name match, and reported it as Lumi Rogue's own token (1B
    supply, 78.6% concentrated) -- Lumi Rogue has never created a token.
    Neither ever checked the found asset's CREATOR against anything
    already established as the entity's (its site's linked wallet, its
    NFD, its verified socials). Same failure shape as the WAD spam-token
    incident (2026-07-16): a coincidental name match with an unrelated
    creator, not a hallucinated number.

    Chains lookup_asset (creator address) -> an NFDomains reverse lookup
    (the creator's .algo name, if it has one) -> lookup_account (how many
    OTHER assets that same address has created -- a one-off/spam mint
    typically has 1; an active project's minting wallet has many) ->
    top current holders. BEFORE attributing a name-matched asset to a
    named entity, compare creator_address/creator_nfd_name here against
    that entity's own established identity. A name match with an
    unrelated creator is not evidence of ownership -- treat it as
    "unrelated token with the same name" unless this actually lines up.
    """
    asset = _tool_lookup_asset(asset_id)
    if asset.get("error"):
        return asset
    creator = asset.get("creator")
    if not creator:
        return {
            "asset_id": asset.get("asset_id"),
            "error": "asset has no creator address on record",
        }

    from app.modules.ai.research_tools import _tool_search_nfd_directory

    identity = _tool_search_nfd_directory(address=creator)
    creator_account = _tool_lookup_account(creator)
    holders = _tool_lookup_asset_holders(asset_id, limit=5)

    return {
        "asset_id": asset.get("asset_id"),
        "asset_name": asset.get("name"),
        "unit_name": asset.get("unit_name"),
        "creator_address": creator,
        "creator_nfd_name": identity.get("name") if identity.get("found") else None,
        "creator_total_assets_created": creator_account.get("total_created_assets"),
        "top_holders": holders.get("top_holders") if not holders.get("error") else None,
        "hint": (
            "Compare creator_address/creator_nfd_name against the entity's OWN "
            "established identity before reporting this as the entity's asset -- "
            "a matching name string alone is not affiliation."
        ),
    }


def _tool_lookup_application(app_id: int | str) -> dict[str, Any]:
    """An application's (smart contract's) creator and DECODED global state — the on-chain variables a protocol exposes (e.g. governance proposal/vote tallies, admin addresses, parameters). Point it at a governance app id to verify what actually executed on-chain."""
    aid = str(app_id).strip()
    if not aid.isdigit():
        return {"error": "app_id must be a numeric application id"}
    from app.core.config import CHAIN_CACHE_TTL_SLOW

    data = _algod_get(f"/v2/applications/{aid}", cache_ttl=CHAIN_CACHE_TTL_SLOW)
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


_BOXES_REQUEST_MAX = 10_000
# Deliberately far above any real box count this tool will meet in practice —
# see _boxes_get for why the request always asks for this many regardless of
# what the caller wants back.


def _boxes_get(base_url: str, token: str, app_id: str, *, cache_ttl: int = 0) -> dict[str, Any]:
    """GET /v2/applications/{id}/boxes, always asking for _BOXES_REQUEST_MAX so the response is reliable either way.

    algod's box-listing endpoint has no truncation flag on a successful
    response — asking for fewer than the true count just silently returns
    that many with nothing telling you more exist. It DOES report the true
    count when the request exceeds either _BOXES_REQUEST_MAX or the node's
    own configured cap: as an HTTP 400 whose body carries "total-boxes"
    (found live 2026-08-07 against the ARC-89 registry app on Testnet).
    Asking for _BOXES_REQUEST_MAX every time means a caller's own max_boxes
    is purely a client-side "how many names to hand back" cap, never masks
    the true total.

    Doesn't go through _algod_get (builds its own URL/params shape), so it
    gets the same cache_ttl contract directly rather than via that chokepoint
    -- cache_ttl > 0 caches a genuine successful body (both the full-list and
    the total-only-on-400 shapes), never {"error": ...}/{"_status": 404}.
    """
    import httpx

    key = (
        _cache_key("boxes", f"{base_url}/v2/applications/{app_id}/boxes", None)
        if cache_ttl > 0
        else ""
    )
    if key:
        cached = _cache_get(key)
        if cached is not None:
            return cached
    headers = {"X-Algo-API-Token": token} if token else {}
    try:
        with httpx.Client(timeout=_TIMEOUT) as http:
            r = http.get(
                f"{base_url}/v2/applications/{app_id}/boxes",
                headers=headers,
                params={"max": _BOXES_REQUEST_MAX},
            )
        if r.status_code == 404:
            return {"_status": 404}
        try:
            data = r.json()
        except Exception:
            data = None
        total = (
            (data.get("data") or {}).get("total-boxes")
            if r.status_code == 400 and isinstance(data, dict)
            else None
        )
        if total is not None:
            result: dict[str, Any] = {"total_boxes": int(total), "boxes": None}
        else:
            r.raise_for_status()
            if not isinstance(data, dict):
                return {"error": "unexpected algod response"}
            boxes = data.get("boxes") or []
            result = {"total_boxes": len(boxes), "boxes": boxes}
        if key:
            _cache_set(key, result, cache_ttl)
        return result
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _tool_application_boxes(
    app_id: int | str, network: str = "mainnet", max_boxes: int = 20
) -> dict[str, Any]:
    """Count and sample an application's box-storage entries — the concrete adoption signal for any box-based registry/state contract (e.g. ARC-89's ASA metadata registry) beyond download counts or spec status: how many things are ACTUALLY registered on-chain. network: 'mainnet' (the operator's own node) or 'testnet' (public AlgoNode, for a Testnet-only deployment). Box names come back base64-encoded raw bytes — decode them yourself per the contract's own key format (e.g. ARC-89 keys each box by an asset's raw 8-byte id)."""
    aid = str(app_id).strip()
    if not aid.isdigit():
        return {"error": "app_id must be a numeric application id"}
    net = (network or "mainnet").strip().lower()
    max_boxes = max(0, min(int(max_boxes), 100))
    from app.core.config import CHAIN_CACHE_TTL_SLOW

    if net == "testnet":
        from app.core.config import TESTNET_ALGOD_URL

        if not TESTNET_ALGOD_URL:
            return {"error": "testnet algod not configured (TESTNET_ALGOD_URL unset)"}
        data = _boxes_get(TESTNET_ALGOD_URL, "", aid, cache_ttl=CHAIN_CACHE_TTL_SLOW)
    elif net == "mainnet":
        from app.core.config import ALGOD_TOKEN, ALGOD_URL

        if not ALGOD_URL:
            return {"error": "algod not configured (ALGOD_URL unset)"}
        data = _boxes_get(ALGOD_URL, ALGOD_TOKEN, aid, cache_ttl=CHAIN_CACHE_TTL_SLOW)
    else:
        return {"error": "network must be 'mainnet' or 'testnet'"}

    if not isinstance(data, dict):
        return {"error": "unexpected algod response"}
    if data.get("error"):
        return data
    if data.get("_status") == 404:
        return {"app_id": int(aid), "network": net, "error": "application not found"}

    total = data.get("total_boxes", 0)
    boxes = data.get("boxes")
    result: dict[str, Any] = {"app_id": int(aid), "network": net, "total_boxes": total}
    if boxes is None:
        # Exceeded the request cap -- algod gave us the true count but not
        # the names. Still the headline adoption number; note why names are
        # missing rather than silently returning an empty list.
        result["box_names"] = []
        result["note"] = (
            f"{total} boxes exceeds what a single request can list names for; "
            "total_boxes is still accurate"
        )
    else:
        result["box_names"] = [
            b.get("name") for b in boxes[:max_boxes] if isinstance(b, dict) and b.get("name")
        ]
    return result


def _tool_get_consensus_stats() -> dict[str, Any]:
    """Algorand consensus participation from algod /v2/ledger/supply: ALGO stake currently ONLINE (securing the network) vs total stake, and the online share. The on-chain measure of participation scale. NOTE: this is online STAKE, not a node count — node count is off-chain telemetry the ledger does not expose."""
    from app.core.config import CHAIN_CACHE_TTL_FAST

    data = _algod_get("/v2/ledger/supply", cache_ttl=CHAIN_CACHE_TTL_FAST)
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


_ROUND_SEARCH_MAX_CALLS = 40


def _block_timestamp(round_number: int) -> tuple[int | None, dict[str, Any] | None]:
    """Fetch one round's block timestamp off the mainnet indexer. Returns (ts, None) on success or (None, error_dict) on failure."""
    from app.core.config import CHAIN_CACHE_TTL_STATIC

    block = _mainnet_idx_get(f"/v2/blocks/{round_number}", cache_ttl=CHAIN_CACHE_TTL_STATIC)
    if not isinstance(block, dict) or block.get("error"):
        err = block if isinstance(block, dict) else {"error": "unexpected indexer response"}
        return None, err
    if block.get("_status") == 404:
        return None, {"error": f"round {round_number} not found (not yet confirmed, or invalid)"}
    ts = block.get("timestamp")
    if ts is None:
        return None, {"error": f"round {round_number}'s block had no timestamp"}
    return ts, None


def _round_to_timestamp(round_number: int) -> dict[str, Any]:
    ts, err = _block_timestamp(round_number)
    if err is not None:
        return {"round": round_number, **err}
    return {"round": round_number, "timestamp_utc": datetime.fromtimestamp(ts, tz=UTC).isoformat()}


def _parse_target_timestamp(date: str) -> tuple[float | None, dict[str, Any] | None]:
    try:
        target_dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
    except ValueError:
        return None, {
            "error": (
                f"could not parse date {date!r} -- use ISO format, "
                "e.g. '2023-01-15' or '2023-01-15T00:00:00Z'"
            )
        }
    if target_dt.tzinfo is None:
        target_dt = target_dt.replace(tzinfo=UTC)
    return target_dt.timestamp(), None


def _current_round() -> tuple[int | None, dict[str, Any] | None]:
    from app.core.config import CHAIN_CACHE_TTL_FAST

    status = _algod_get("/v2/status", cache_ttl=CHAIN_CACHE_TTL_FAST)
    if not isinstance(status, dict) or status.get("error"):
        return None, (
            status if isinstance(status, dict) else {"error": "unexpected algod response"}
        )
    hi = status.get("last-round", status.get("lastRound"))
    if not hi:
        return None, {"error": "could not read current round from algod status"}
    return hi, None


def _date_to_nearest_round(date: str) -> dict[str, Any]:
    target_ts, err = _parse_target_timestamp(date)
    if err is not None:
        return err

    hi, err = _current_round()
    if err is not None:
        return err

    lo = 1
    hi_ts, err = _block_timestamp(hi)
    if err is not None:
        return err
    lo_ts, err = _block_timestamp(lo)
    if err is not None:
        return err

    if target_ts >= hi_ts:
        return _round_date_result(
            date, hi, hi_ts, "date is at/after the latest confirmed round; clamped to it"
        )
    if target_ts <= lo_ts:
        return _round_date_result(date, lo, lo_ts, "date is at/before genesis; clamped to round 1")

    calls = 0
    while hi - lo > 1 and calls < _ROUND_SEARCH_MAX_CALLS:
        mid = (lo + hi) // 2
        mid_ts, err = _block_timestamp(mid)
        calls += 1
        if err is not None:
            return err
        if mid_ts < target_ts:
            lo, lo_ts = mid, mid_ts
        else:
            hi, hi_ts = mid, mid_ts

    nearest_round, nearest_ts = (
        (lo, lo_ts) if abs(lo_ts - target_ts) <= abs(hi_ts - target_ts) else (hi, hi_ts)
    )
    return _round_date_result(date, nearest_round, nearest_ts)


def _round_date_result(date: str, round_number: int, ts: int, note: str = "") -> dict[str, Any]:
    result = {
        "date": date,
        "nearest_round": round_number,
        "round_timestamp_utc": datetime.fromtimestamp(ts, tz=UTC).isoformat(),
    }
    if note:
        result["note"] = note
    return result


def _tool_round_to_date(round_number: int | None = None, date: str | None = None) -> dict[str, Any]:
    """Convert between an Algorand round number and a calendar date/time.

    Neither algod nor the indexer expose this conversion directly, so a claim
    like "created at round 12345678" or "the asset launched in early 2023"
    could only be reported as-is, not cross-checked against each other or a
    news date (self-reported gap, 2026-08-10: a "world population tracker"
    balance's growth curve since asset launch needed a round->date anchor).

    Pass exactly one of round_number (-> its block's UTC timestamp) or date
    (an ISO date/datetime -> the nearest round, found by binary search
    against real block timestamps). Binary search, not a fixed average block
    time, because Algorand's block time has shifted across its history
    (~4.5s early on, ~2.8s more recently) -- an average would drift by weeks
    over a multi-year span.
    """
    if (round_number is None) == (date is None):
        return {"error": "pass exactly one of round_number or date"}
    if round_number is not None:
        return _round_to_timestamp(round_number)
    return _date_to_nearest_round(date)


def _testnet_idx_get(
    path: str, params: dict | None = None, *, cache_ttl: int = 0
) -> dict[str, Any]:
    """GET a path off the public testnet INDEXER (history-capable, unlike algod).

    Returns parsed JSON, {"_status": 404} for a missing entity, or {"error": ...}.
    cache_ttl > 0 caches a genuine successful body -- see _algod_get's
    docstring for the caching contract, identical here.
    """
    import httpx

    from app.core.config import TESTNET_INDEXER_URL

    if not TESTNET_INDEXER_URL:
        return {"error": "testnet indexer not configured (TESTNET_INDEXER_URL unset)"}
    key = _cache_key("testnet_idx", path, params) if cache_ttl > 0 else ""
    if key:
        cached = _cache_get(key)
        if cached is not None:
            return cached
    try:
        with httpx.Client(timeout=_TIMEOUT) as http:
            r = http.get(f"{TESTNET_INDEXER_URL}{path}", params=params)
        if r.status_code == 404:
            return {"_status": 404}
        r.raise_for_status()
        result = r.json()
        if key and isinstance(result, dict) and not result.get("error"):
            _cache_set(key, result, cache_ttl)
        return result
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _testnet_lookup_txid(txid: str) -> dict[str, Any]:
    from app.core.config import CHAIN_CACHE_TTL_STATIC

    data = _testnet_idx_get(f"/v2/transactions/{txid}", cache_ttl=CHAIN_CACHE_TTL_STATIC)
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
    from app.core.config import CHAIN_CACHE_TTL_SLOW

    data = _testnet_idx_get(f"/v2/applications/{app_id}", cache_ttl=CHAIN_CACHE_TTL_SLOW)
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
    from app.core.config import CHAIN_CACHE_TTL_FAST

    acct = _testnet_idx_get(f"/v2/accounts/{address}", cache_ttl=CHAIN_CACHE_TTL_FAST)
    if not isinstance(acct, dict) or acct.get("error"):
        return acct if isinstance(acct, dict) else {"error": "unexpected indexer response"}
    if acct.get("_status") == 404:
        return {"address": address, "found": False, "error": "account not found on testnet"}
    a = acct.get("account", {}) or {}
    txns = _testnet_idx_get(
        f"/v2/accounts/{address}/transactions", params={"limit": 10}, cache_ttl=CHAIN_CACHE_TTL_FAST
    )
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
                "Live on-chain state of an Algorand account: ALGO balance, ASAs held, "
                "apps created/opted into, auth_addr, sig_type. Use to verify holdings, "
                "treasury balances, or protocol participation. auth_addr is set only "
                "if the account was REKEYED to a different signer — two accounts "
                "sharing the same auth_addr are under common control even with "
                "different addresses/creators. sig_type: sig=personal wallet, "
                "msig=multisig, lsig=contract-controlled. The address MUST come from "
                "a fetched page, search result, or another tool's output — never "
                "guess or pattern-match one (e.g. a project name as prefix); if you "
                "don't have a real address, say so instead of inventing one. "
                "created_assets is paginated 25 at a time -- total_created_assets is "
                "always the true count; if created_assets_has_more is true, call again "
                "with created_assets_offset to see the rest (e.g. to confirm the exact "
                "id range of a prolific creator's full NFT series)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "58-char Algorand address"},
                    "created_assets_offset": {
                        "type": "integer",
                        "description": (
                            "Page offset into created_assets, 25 per page. Default 0 (first page)."
                        ),
                    },
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
                "to verify what executed on-chain rather than relying on a forum post. This "
                "(with lookup_account and lookup_asset) is how you query algod/the indexer "
                "directly — there is no separate raw 'algod' tool."
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
                "asset's metadata url. A name match is NOT proof of affiliation — a "
                "top result can be an unrelated token that happens to share a name. "
                "Before reporting a result here as a named entity's OWN token, call "
                "trace_asset_creator on it and compare the creator against that "
                "entity's own established identity."
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
            "name": "lookup_transaction_note",
            "description": (
                "The note field of one specific transaction, by its txid, via "
                "the mainnet indexer — use once a specific memo's exact "
                "wording matters to a claim (a payment reference, a "
                "governance vote rationale, an on-chain message), instead of "
                "trusting a third party's paraphrase of it. Most real memos "
                "are UTF-8 text and come back decoded directly; non-text "
                "notes come back as base64 with is_utf8_text=false."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "txid": {"type": "string", "description": "the transaction's id"},
                },
                "required": ["txid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_arc69_metadata",
            "description": (
                "An ASA's ARC-69 attributes/traits (e.g. a game rating, rarity "
                "trait), read from the note field of its most recent "
                "asset-config transaction — NOT lookup_asset's url field, "
                "which for ARC-69 is only the artwork, not the attributes. "
                "Use whenever a claim depends on structured NFT metadata "
                "(a stat, a rating, a trait) that the standard writes "
                "on-chain, instead of reporting it as unverifiable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "integer", "description": "the ASA's numeric id"},
                },
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_asset_transactions",
            "description": (
                "Recent transaction/transfer history for a specific ASA, via the "
                "mainnet indexer — marketplace-agnostic on-chain event history "
                "(creation, every transfer, to/from whom), unlike "
                "lookup_asset_holders which is a current-snapshot only. Use to "
                "check how much something has actually traded, independent of a "
                "marketplace's own listing page. amount_raw is unconverted. "
                "A zero-amount entry is routine opt-in/opt-out, not a real "
                "transfer — is_real_transfer flags which is which; trust "
                "most_recent_real_transfer_round_time, not the newest entry alone. "
                "Check is_recent_window: true = genuinely recent data (trustworthy "
                "for 'is this moving now'); false = no recent activity found, so "
                "this fell back to the OLDEST page from the asset's creation — for "
                "a high-volume asset that's early history, not evidence of "
                "inactivity, so don't report it as 'no transfers since <date>' "
                "without that caveat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "integer", "description": "numeric ASA id"},
                    "limit": {
                        "type": "integer",
                        "description": "1-30 most recent transactions to return, default 10",
                    },
                },
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_asset_transaction_volume",
            "description": (
                "Aggregate transaction count + total amount moved for an ASA "
                "since a given round (default: since creation) — for checking a "
                "headline claim like '3.5M transactions' or '$10B in volume' "
                "that lookup_asset_transactions' single page can't verify. Pages "
                "through the indexer (slow — up to several seconds). complete: "
                "true means transaction_count/total_amount_moved_raw ARE the "
                "true lifetime totals; false means it hit the page cap first, "
                "so the counts are only a LOWER BOUND — still enough to "
                "falsify an inflated claim ('at least 25,000 transfers'), just "
                "not to confirm an exact one. total_amount_moved_raw is in RAW "
                "base units; pair with lookup_asset's decimals to adjust it, "
                "and with round_to_date to scope min_round to a calendar date."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "integer", "description": "numeric ASA id"},
                    "min_round": {
                        "type": "integer",
                        "description": "only count transactions at/after this round, default 0 (since creation)",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "indexer pages (1000 txns each) to fetch before giving up, 1-100, default 25",
                    },
                },
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nft_collection_distribution_timeline",
            "description": (
                "WHEN each item in an NFT collection was first sent out by "
                "its creator, from real on-chain transfer history -- a "
                "genuine 'is adoption rising, flat, or stalled since mint' "
                "trend. Does NOT return a price: confirmed live 2026-08-15 "
                "that not every collection's distribution is a priced "
                "atomic-swap sale -- some (e.g. a project accepting "
                "off-chain payment before sending the NFT) have no ALGO "
                "price recoverable from the chain at all. Don't guess a "
                "price from this tool's output; it only tells you when "
                "items moved and to whom. Each item is flagged resold_since "
                "if it changed hands again after the creator's initial "
                "send (a DIFFERENT, potentially price-bearing event this "
                "tool doesn't itself investigate). Bounded by max_assets "
                "(default 40, cap 100) for a large collection -- 'sampled' "
                "vs 'total_created_assets' in the response makes clear "
                "whether this is a full census or a trend sample; pass "
                "asset_ids explicitly to check specific items instead. "
                "Slow (one transaction-history fetch per sampled asset)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "creator_address": {
                        "type": "string",
                        "description": "the collection creator's Algorand address",
                    },
                    "max_assets": {
                        "type": "integer",
                        "description": "how many created assets to sample (oldest-first), default 40, max 100 -- ignored if asset_ids is given",
                    },
                    "asset_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "optional: specific ASA ids to check instead of sampling from created_assets",
                    },
                },
                "required": ["creator_address"],
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
            "name": "trace_asset_creator",
            "description": (
                "Resolve an ASA's REAL creator identity -- its creator address, "
                "that address's .algo name (if any), how many OTHER assets it has "
                "created, and current top holders. Use this BEFORE reporting a "
                "name-matched asset (from lookup_asset_by_name) as belonging to a "
                "named entity: a matching name string alone is not affiliation — "
                "compare the returned creator_address/creator_nfd_name against the "
                "entity's own established identity (its site's linked wallet, its "
                "NFD, its verified socials) first. A coincidental name match with "
                "an unrelated creator has produced real fabricated-ownership "
                "articles before (a spam token wrongly cited as a project's own)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "integer", "description": "numeric ASA id"},
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
    {
        "type": "function",
        "function": {
            "name": "application_boxes",
            "description": (
                "Count and sample an Algorand application's box-storage entries — the "
                "concrete adoption number for any box-based registry/state contract "
                "(e.g. an ARC-89 ASA metadata registry): how many things are ACTUALLY "
                "registered on-chain, not just download counts or spec status. Box "
                "names come back base64-encoded raw bytes; decode per the contract's "
                "own key format if it defines one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_id": {"type": "integer", "description": "numeric application id"},
                    "network": {
                        "type": "string",
                        "description": "'mainnet' (default) or 'testnet'",
                    },
                    "max_boxes": {
                        "type": "integer",
                        "description": "how many box names to return, 0-100, default 20 (total_boxes is always the true count regardless)",
                    },
                },
                "required": ["app_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "round_to_date",
            "description": (
                "Convert between an Algorand round number and a calendar date/time, "
                "so a claim like 'created at round 12345678' or 'launched in early "
                "2023' can be cross-checked against a news date or another round. "
                "Pass exactly ONE of round_number or date. date -> round uses binary "
                "search against real block timestamps (not a fixed average block "
                "time), so it can take several seconds."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "round_number": {
                        "type": "integer",
                        "description": "a round number -> its block's UTC timestamp",
                    },
                    "date": {
                        "type": "string",
                        "description": (
                            "an ISO date/datetime, e.g. '2023-01-15' or "
                            "'2023-01-15T00:00:00Z' -> the nearest round"
                        ),
                    },
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
    "lookup_transaction_note": _tool_lookup_transaction_note,
    "lookup_arc69_metadata": _tool_lookup_arc69_metadata,
    "lookup_asset_transactions": _tool_lookup_asset_transactions,
    "get_asset_transaction_volume": _tool_get_asset_transaction_volume,
    "nft_collection_distribution_timeline": _tool_nft_collection_distribution_timeline,
    "lookup_application": _tool_lookup_application,
    "application_boxes": _tool_application_boxes,
    "get_asset_holder_share": _tool_get_asset_holder_share,
    "lookup_asset_holders": _tool_lookup_asset_holders,
    "trace_asset_creator": _tool_trace_asset_creator,
    "get_consensus_stats": _tool_get_consensus_stats,
    "testnet_lookup": _tool_testnet_lookup,
    "round_to_date": _tool_round_to_date,
}


def chain_tools() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """On-chain lookup tools (schemas, handlers). Always registered — the handlers degrade gracefully to {"error": ...} when ALGOD_URL is unset."""
    return list(CHAIN_SCHEMAS), dict(CHAIN_HANDLERS)
