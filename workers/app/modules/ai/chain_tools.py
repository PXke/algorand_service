"""Read-only on-chain lookups backed by the wired algod node (ALGOD_URL/TOKEN,
the same connector chain_reader.py uses). These answer the recurring "verify it
on-chain" gap the writer kept working around — point lookup_application at a
governance app to read its live proposal/vote state, etc.

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


def _algod_get(path: str) -> Any:
    """GET a path off the operator-configured (trusted) algod node. Returns the
    parsed JSON, {"_status": 404} for a missing entity, or {"error": ...}."""
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
    """32-byte public key -> 58-char Algorand address (base32 of key + 4-byte
    sha512/256 checksum)."""
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


def _decode_value(v: dict) -> Any:
    """TEAL state value: type 2 = uint, type 1 = bytes (shown as ASCII, a 32-byte
    address, or hex — best-effort so the model can read governance/admin fields)."""
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
    """58-char Algorand address format + checksum validity — the inverse of
    _encode_address. Rejects a fabricated address BEFORE it reaches algod,
    with a clear reason, instead of algod's generic 400 (root-caused
    2026-07-14: the model invented plausible-looking addresses like
    'EXA6RX5G...' for projects it had no real address for — three of the
    four weren't even the right length, and none had a chance of a valid
    checksum since nothing was actually generated)."""
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
    """Live state of an Algorand account: ALGO balance, ASAs held, and the apps it
    created or opted into."""
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
    return {
        "address": addr,
        "balance_algo": round((data.get("amount", 0) or 0) / 1e6, 6),
        "min_balance_algo": round((data.get("min-balance", 0) or 0) / 1e6, 6),
        "status": data.get("status"),
        "total_assets_held": len(assets),
        "assets": [
            {"asset_id": a.get("asset-id"), "amount": a.get("amount")}
            for a in assets[:25]
            if isinstance(a, dict)
        ],
        "created_assets": [
            a.get("index")
            for a in (data.get("created-assets", []) or [])[:25]
            if isinstance(a, dict)
        ],
        "created_apps": [
            a.get("id") for a in (data.get("created-apps", []) or [])[:25] if isinstance(a, dict)
        ],
        "opted_in_apps": [
            a.get("id")
            for a in (data.get("apps-local-state", []) or [])[:25]
            if isinstance(a, dict)
        ],
    }


def _tool_lookup_asset(asset_id: Any) -> dict[str, Any]:
    """Algorand Standard Asset (ASA) parameters: name, supply, decimals, creator,
    and the manager/freeze/clawback/reserve roles."""
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
    total = p.get("total")
    decimals = p.get("decimals")
    # total/decimals are raw base units — division-by-10**decimals done here,
    # not left to the model. Doing it in-prompt is exactly how a real incident
    # happened (2026-07-14): the writer manually converted a 15-digit raw ASA
    # total and got the decimal shift wrong, reporting "1 trillion" for what
    # total_adjusted below correctly computes as 1 billion.
    total_adjusted = None
    if isinstance(total, int | float) and isinstance(decimals, int) and decimals >= 0:
        total_adjusted = round(total / (10**decimals), 6)
    return {
        "asset_id": int(aid),
        "name": p.get("name"),
        "unit_name": p.get("unit-name"),
        "total": total,
        "decimals": decimals,
        "total_adjusted": total_adjusted,
        "creator": p.get("creator"),
        "url": p.get("url"),
        "default_frozen": p.get("default-frozen"),
        "manager": p.get("manager"),
        "freeze": p.get("freeze"),
        "clawback": p.get("clawback"),
        "reserve": p.get("reserve"),
    }


def _mainnet_idx_get(path: str, params: dict | None = None) -> Any:
    """GET a path off the public MAINNET indexer (name-search capable, unlike
    algod). Returns parsed JSON, {"_status": 404} for a missing entity, or
    {"error": ...}."""
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
    """Search mainnet ASAs by ticker/unit-name (preferred) or display name when
    the numeric asset_id isn't known yet — lookup_asset needs an id, and algod
    itself can't search by name (only the indexer can). Use this first to find
    the id, then lookup_asset for the full parameters.

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
    "CompX")."""
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
        results.append({
            "asset_id": a.get("index"),
            "name": p.get("name"),
            "unit_name": p.get("unit-name"),
            "creator": p.get("creator"),
        })
    return {"query": q, "results": results[:n]}


def _tool_get_asset_holder_share(asset_id: Any, address: str) -> dict[str, Any]:
    """A specific address's share of an ASA's total supply, computed here (not
    left to the model) — use this instead of manually dividing lookup_asset's
    total by lookup_account's raw holding, which is exactly how a real
    fabricated "99.99%" concentration claim happened (2026-07-14): the model
    got the decimal-shift arithmetic wrong on a 15-digit raw amount."""
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


def _tool_lookup_application(app_id: Any) -> dict[str, Any]:
    """An application's (smart contract's) creator and DECODED global state — the
    on-chain variables a protocol exposes (e.g. governance proposal/vote tallies,
    admin addresses, parameters). Point it at a governance app id to verify what
    actually executed on-chain."""
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
    """Algorand consensus participation from algod /v2/ledger/supply: ALGO stake
    currently ONLINE (securing the network) vs total stake, and the online share.
    The on-chain measure of participation scale. NOTE: this is online STAKE, not a
    node count — node count is off-chain telemetry the ledger does not expose."""
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


def _testnet_idx_get(path: str, params: dict | None = None) -> Any:
    """GET a path off the public testnet INDEXER (history-capable, unlike algod).
    Returns parsed JSON, {"_status": 404} for a missing entity, or {"error": ...}."""
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


def _tool_testnet_lookup(
    txid: str = "", address: str = "", app_id: Any = "",
) -> dict[str, Any]:
    """Verify Testnet on-chain activity via the public testnet indexer. Pass EXACTLY
    one of: a transaction id (confirm a tx happened and what it did), an account
    address (recent activity + what it created), or an application id (confirm a
    contract is deployed and when). Testnet only — for mainnet use lookup_* tools."""
    txid = (txid or "").strip()
    address = (address or "").strip()
    app_id = str(app_id).strip()

    if txid:
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

    if app_id:
        if not app_id.isdigit():
            return {"error": "app_id must be a numeric application id"}
        data = _testnet_idx_get(f"/v2/applications/{app_id}")
        if not isinstance(data, dict) or data.get("error"):
            return data if isinstance(data, dict) else {"error": "unexpected indexer response"}
        if data.get("_status") == 404:
            return {
                "app_id": int(app_id),
                "found": False,
                "error": "application not found on testnet",
            }
        app = data.get("application", {}) or {}
        return {
            "app_id": int(app_id),
            "found": True,
            "creator": (app.get("params", {}) or {}).get("creator"),
            "created_at_round": app.get("created-at-round"),
            "deleted": app.get("deleted", False),
            "deleted_at_round": app.get("deleted-at-round"),
        }

    if address:
        if not _is_valid_address(address):
            return {"address": address, "error": _INVALID_ADDRESS_ERROR}
        acct = _testnet_idx_get(f"/v2/accounts/{address}")
        if not isinstance(acct, dict) or acct.get("error"):
            return acct if isinstance(acct, dict) else {"error": "unexpected indexer response"}
        if acct.get("_status") == 404:
            return {"address": address, "found": False, "error": "account not found on testnet"}
        a = acct.get("account", {}) or {}
        txns = _testnet_idx_get(
            f"/v2/accounts/{address}/transactions", params={"limit": 10}
        )
        recent = []
        if isinstance(txns, dict) and not txns.get("error"):
            for t in (txns.get("transactions", []) or [])[:10]:
                if isinstance(t, dict):
                    recent.append({
                        "txid": t.get("id"),
                        "type": t.get("tx-type"),
                        "round": t.get("confirmed-round"),
                        "round_time": t.get("round-time"),
                    })
        return {
            "address": address,
            "found": True,
            "balance_algo": round((a.get("amount", 0) or 0) / 1e6, 6),
            "created_apps": a.get("total-created-apps"),
            "created_assets": a.get("total-created-assets"),
            "apps_opted_in": a.get("total-apps-opted-in"),
            "recent_transactions": recent,
        }

    return {"error": "pass one of txid, address, or app_id"}


CHAIN_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_account",
            "description": (
                "Live on-chain state of an Algorand account by address: ALGO balance, "
                "ASAs held, and the apps it created or opted into. Use to verify holdings, "
                "treasury balances, or whether an account participates in a protocol. "
                "The address MUST be one you actually found in a fetched page, search "
                "result, or another tool's output — never construct, guess, or "
                "pattern-match a plausible-looking one yourself (e.g. 'the project's name "
                "as a prefix'). If you don't have a real address for this project, say so "
                "in the article instead of inventing one to check."
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
                "decimals, creator, and manager/freeze/clawback/reserve roles. Use to verify "
                "a token's real supply or who controls it."
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
                "an id and can't search by name). Returns candidate asset_ids to pass "
                "into lookup_asset for full parameters."
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
    "lookup_application": _tool_lookup_application,
    "get_asset_holder_share": _tool_get_asset_holder_share,
    "get_consensus_stats": _tool_get_consensus_stats,
    "testnet_lookup": _tool_testnet_lookup,
}


def chain_tools() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """On-chain lookup tools (schemas, handlers). Always registered — the handlers
    degrade gracefully to {"error": ...} when ALGOD_URL is unset."""
    return list(CHAIN_SCHEMAS), dict(CHAIN_HANDLERS)
