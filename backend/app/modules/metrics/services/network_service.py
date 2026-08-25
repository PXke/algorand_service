"""Live algod node status and Nodely node-count telemetry for network tiles."""

from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import Any

import httpx

from app.core.cache import cached_json
from app.core.config import settings

logger = logging.getLogger(__name__)

_NODELY_DS_QUERY = "https://g.nodely.io/api/ds/query"
_NODELY_CH_UID = "fc25640e-50ee-4e04-aad6-2a5336c09eaf"
# Shared Redis TTL — at most one Nodely pull per hour across all workers.
_NODELY_CACHE_KEY = "metrics:nodely-node-count"
_NODELY_CACHE_TTL = 3600


_ROUND_TIME_CACHE_KEY = "metrics:round-time"
_ROUND_TIME_CACHE_TTL = 120
# Averaged over a span rather than one gap: consensus jitter makes a single
# interval swing by hundreds of ms, and the tile should read as a network
# characteristic, not a stopwatch.
_ROUND_TIME_SPAN = 20


_ALGOD_STATUS_CACHE_KEY = "metrics:algod-status"
_ALGOD_STATUS_CACHE_TTL = 10

# Front-page pulse: ~20 rounds is a minute at ~2.8s. Need one extra header so
# txn count is a tc delta, not a guess from the payset (header-only omits it).
_PULSE_BARS = 20
_PULSE_CACHE_KEY = "metrics:chain-pulse"
_PULSE_CACHE_TTL = 2

_pulse_lock = threading.Lock()
_pulse_ring: list[dict[str, int | None]] = []
# Per-round txn mix (pay/axfer/appl segments) — fetched once per round while it
# stays in the 20-bar window; steady-state polls only pull ~0–1 new full blocks.
_pulse_mix_cache: dict[int, dict[str, Any]] = {}


def _algod_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = settings.algod_token.strip()
    if token:
        headers["X-Algo-API-Token"] = token
    return headers


@lru_cache(maxsize=1)
def _http_client() -> httpx.Client:
    """Shared client so dashboard polls reuse connections."""
    return httpx.Client(timeout=8.0)


def _block_timestamp(round_number: int, *, timeout: float) -> int | None:
    """Unix seconds the given round was committed, or None if algod won't say."""
    url = f"{settings.algod_url.rstrip('/')}/v2/blocks/{round_number}"
    try:
        response = _http_client().get(
            url, headers=_algod_headers(), params={"format": "json"}, timeout=timeout
        )
        response.raise_for_status()
        block = response.json().get("block")
        ts = block.get("ts") if isinstance(block, dict) else None
        return int(ts) if isinstance(ts, int | float) else None
    except Exception as exc:
        logger.warning("Algod block %s fetch failed: %s", round_number, exc)
        return None


def fetch_round_time_seconds(last_round: int, *, timeout: float = 8.0) -> float | None:
    """Mean seconds per round over the last `_ROUND_TIME_SPAN` rounds.

    Not `time-since-last-round` from /v2/status: that is a stopwatch on the
    CURRENT block, so polling it returns whatever instant the request landed on
    (observed 2.2s, 1.8s, 1.8s within seconds of each other) — the age of one
    block, not how long a round takes. Two block headers and a subtraction give
    the real figure, which for Algorand sits near 2.8s.
    """
    if last_round <= _ROUND_TIME_SPAN:
        return None

    def compute() -> float | None:
        newest = _block_timestamp(last_round, timeout=timeout)
        oldest = _block_timestamp(last_round - _ROUND_TIME_SPAN, timeout=timeout)
        if newest is None or oldest is None or newest <= oldest:
            return None
        return (newest - oldest) / _ROUND_TIME_SPAN

    # Keyed on the span, not the round: the value barely moves, and this keeps
    # every browser polling the dashboard from issuing two algod calls a minute.
    return cached_json(_ROUND_TIME_CACHE_KEY, _ROUND_TIME_CACHE_TTL, compute)


def _fetch_algod_status_uncached(*, timeout: float = 8.0) -> dict[str, Any]:
    url = settings.algod_url.rstrip("/") + "/v2/status"
    response = _http_client().get(url, headers=_algod_headers(), timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return {}
    return payload


def fetch_algod_status(*, timeout: float = 8.0) -> dict[str, Any]:
    """Best-effort Algod /v2/status for chain activity tiles (short Redis TTL)."""
    try:
        return cached_json(
            _ALGOD_STATUS_CACHE_KEY,
            _ALGOD_STATUS_CACHE_TTL,
            lambda: _fetch_algod_status_uncached(timeout=timeout),
        )
    except Exception as exc:
        logger.warning("Algod status fetch failed: %s", exc)
        return {}


def _block_header(round_number: int, *, timeout: float = 8.0) -> dict[str, int | None] | None:
    """Header-only: round, unix ts, and the cumulative txn counter (`tc`)."""
    url = f"{settings.algod_url.rstrip('/')}/v2/blocks/{round_number}"
    try:
        response = _http_client().get(
            url,
            headers=_algod_headers(),
            params={"format": "json", "header-only": "true"},
            timeout=timeout,
        )
        response.raise_for_status()
        block = response.json().get("block")
        if not isinstance(block, dict):
            return None
        rnd = block.get("rnd", round_number)
        ts = block.get("ts")
        tc = block.get("tc")
        if not isinstance(rnd, int) or not isinstance(tc, int):
            return None
        return {
            "round": rnd,
            "tc": tc,
            "ts": int(ts) if isinstance(ts, int | float) else None,
        }
    except Exception as exc:
        logger.warning("Algod block header %s fetch failed: %s", round_number, exc)
        return None


_KIND_BUCKET = {
    "pay": "pay",
    "axfer": "axfer",
    "acfg": "axfer",
    "afrz": "axfer",
    "appl": "appl",
    "keyreg": "keyreg",
    "stpf": "stpf",
}
_KIND_ORDER = ("pay", "axfer", "appl", "keyreg", "stpf", "other")


def summarize_block(round_number: int, block: dict[str, Any]) -> dict[str, Any]:
    """Editorial mix for one round: payments / assets / apps, plus inner count."""
    txns = block.get("txns")
    wrappers = txns if isinstance(txns, list) else []
    top: dict[str, int] = {}
    inners = 0

    def walk(items: list[Any], *, inner: bool) -> None:
        nonlocal inners
        for wrap in items:
            if not isinstance(wrap, dict):
                continue
            txn = wrap.get("txn")
            if not isinstance(txn, dict):
                txn = wrap if "type" in wrap else {}
            raw = str(txn.get("type") or "").lower()
            bucket = _KIND_BUCKET.get(raw, "other") if raw else "other"
            if inner:
                inners += 1
            else:
                top[bucket] = top.get(bucket, 0) + 1
            dt = wrap.get("dt")
            if isinstance(dt, dict):
                itx = dt.get("itx")
                if isinstance(itx, list) and itx:
                    walk(itx, inner=True)

    walk(wrappers, inner=False)
    ts = block.get("ts")
    return {
        "round": round_number,
        "txns": len(wrappers),
        "inners": inners,
        "ts": int(ts) if isinstance(ts, int | float) else None,
        "kinds": [{"id": k, "count": top[k]} for k in _KIND_ORDER if top.get(k)],
    }


def _fetch_block_json(round_number: int, *, timeout: float = 12.0) -> dict[str, Any] | None:
    """Full block JSON (payset included). None if algod has no such round."""
    url = f"{settings.algod_url.rstrip('/')}/v2/blocks/{round_number}"
    response = _http_client().get(
        url,
        headers=_algod_headers(),
        params={"format": "json"},
        timeout=timeout,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    block = payload.get("block") if isinstance(payload, dict) else None
    return block if isinstance(block, dict) else None


def fetch_block_composition(round_number: int, *, timeout: float = 12.0) -> dict[str, Any] | None:
    """Per-round txn mix, cached briefly so a click does not re-hit algod."""

    def compute() -> dict[str, Any]:
        block = _fetch_block_json(round_number, timeout=timeout)
        if block is None:
            raise LookupError("block missing")
        return summarize_block(round_number, block)

    try:
        return cached_json(f"metrics:block-mix:{round_number}", 45, compute)
    except LookupError:
        return None
    except Exception as exc:
        logger.warning("Algod block %s composition failed: %s", round_number, exc)
        return None


def reset_chain_pulse_ring() -> None:
    """Test helper — drop the in-process header ring and composition cache."""
    global _pulse_ring, _pulse_mix_cache
    with _pulse_lock:
        _pulse_ring = []
        _pulse_mix_cache = {}


def fetch_chain_pulse(*, timeout: float = 8.0) -> dict[str, Any]:
    """Last ~minute of committed rounds with per-block txn counts.

    Cached two seconds so every open homepage shares one algod poll. The ring
    of headers lives in-process: a new round costs one header fetch, not twenty.
    """

    def compute() -> dict[str, Any]:
        try:
            status = _fetch_algod_status_uncached(timeout=timeout)
        except Exception as exc:
            logger.warning("Algod status for chain pulse failed: %s", exc)
            return {"last_round": 0, "txns_last_minute": 0, "blocks": []}
        last = status.get("last-round", status.get("LastRound"))
        if not isinstance(last, int) or last <= _PULSE_BARS:
            return {"last_round": 0, "txns_last_minute": 0, "blocks": []}

        want = set(range(last - _PULSE_BARS, last + 1))
        with _pulse_lock:
            have = {int(b["round"]) for b in _pulse_ring if isinstance(b.get("round"), int)}
            missing = sorted(want - have)

        fetched: list[dict[str, int | None]] = []
        if missing:
            from concurrent.futures import ThreadPoolExecutor

            workers = min(8, len(missing))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for header in pool.map(
                    lambda rnd: _block_header(rnd, timeout=timeout), missing
                ):
                    if header is not None:
                        fetched.append(header)

        with _pulse_lock:
            by_round = {
                int(b["round"]): b
                for b in (*_pulse_ring, *fetched)
                if isinstance(b.get("round"), int)
            }
            kept = [by_round[r] for r in sorted(want) if r in by_round]
            _pulse_ring.clear()
            _pulse_ring.extend(kept)
            ring = list(_pulse_ring)

        tc_by_round = {
            int(b["round"]): int(b["tc"])
            for b in ring
            if isinstance(b.get("round"), int) and isinstance(b.get("tc"), int)
        }
        ts_by_round = {
            int(b["round"]): (int(b["ts"]) if isinstance(b.get("ts"), int) else None)
            for b in ring
            if isinstance(b.get("round"), int)
        }
        blocks: list[dict[str, Any]] = []
        for rnd in range(last - _PULSE_BARS + 1, last + 1):
            cur = tc_by_round.get(rnd)
            prev = tc_by_round.get(rnd - 1)
            txns = (cur - prev) if cur is not None and prev is not None and cur >= prev else 0
            blocks.append({"round": rnd, "txns": txns, "ts": ts_by_round.get(rnd)})

        display = [int(b["round"]) for b in blocks if isinstance(b.get("round"), int)]
        with _pulse_lock:
            for stale in [r for r in _pulse_mix_cache if r not in want]:
                del _pulse_mix_cache[stale]
            need_mix = [rnd for rnd in display if rnd not in _pulse_mix_cache]

        if need_mix:
            from concurrent.futures import ThreadPoolExecutor

            workers = min(8, len(need_mix))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for mix in pool.map(
                    lambda rnd: fetch_block_composition(rnd, timeout=timeout),
                    need_mix,
                ):
                    if isinstance(mix, dict) and isinstance(mix.get("round"), int):
                        with _pulse_lock:
                            _pulse_mix_cache[int(mix["round"])] = mix

        with _pulse_lock:
            mixes = {rnd: _pulse_mix_cache[rnd] for rnd in display if rnd in _pulse_mix_cache}

        for block in blocks:
            mix = mixes.get(int(block["round"]))
            kinds = mix.get("kinds") if mix else None
            block["kinds"] = kinds if isinstance(kinds, list) else []
            block["inners"] = int(mix.get("inners") or 0) if mix else 0

        total = sum(int(b["txns"] or 0) for b in blocks)
        return {"last_round": last, "txns_last_minute": total, "blocks": blocks}

    return cached_json(_PULSE_CACHE_KEY, _PULSE_CACHE_TTL, compute)


def _fetch_nodely_node_stats_uncached(*, timeout: float = 8.0) -> dict[str, Any]:
    """Hit Nodely once. Raises on failure so Redis does not cache a miss for an hour."""
    import time

    now_ms = int(time.time() * 1000)
    body = {
        "from": str(now_ms - 30 * 86_400_000),
        "to": str(now_ms),
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "grafana-clickhouse-datasource", "uid": _NODELY_CH_UID},
                "rawSql": "select * from nodely.v_node_cnt_daily order by ts desc limit 7",
                "format": 1,
                "queryType": "table",
                "intervalMs": 86_400_000,
                "maxDataPoints": 100,
            }
        ],
    }
    response = _http_client().post(_NODELY_DS_QUERY, json=body, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    frame = data["results"]["A"]["frames"][0]
    fields = [f["name"] for f in frame["schema"]["fields"]]
    cols = frame["data"]["values"]
    idx = {name: i for i, name in enumerate(fields)}
    if "nodes" not in idx or not cols or not cols[idx["nodes"]]:
        raise ValueError(f"missing ts/nodes columns (got {fields})")

    count = int(cols[idx["nodes"]][0])
    if count <= 0:
        raise ValueError(f"non-positive node count: {count}")

    hint = "Nodely"
    if len(cols[idx["nodes"]]) >= 2:
        try:
            prev = int(cols[idx["nodes"]][1])
            if prev > 0:
                delta = count - prev
                if delta != 0:
                    sign = "+" if delta > 0 else ""
                    hint = f"{sign}{delta} vs prior day"
        except (TypeError, ValueError):
            pass

    return {"node_count": count, "hint": hint, "source": "g.nodely.io"}


def fetch_nodely_node_stats(*, timeout: float = 8.0) -> dict[str, Any]:
    """Latest daily full-time mainnet node estimate from Nodely (Chao-1).

    Off-chain telemetry — the ledger does not expose a node count of any kind
    (Algorand's PPoS has no on-chain validator registry to query). This is
    Nodely's estimated *total* full-time node population — participation
    nodes, API nodes, and bot connections all counted without discrimination
    by node type — not a relay-only count (Foundation-run relays number in
    the low hundreds) and not a per-round voting-committee size. It is the
    same figure the Algorand Foundation's own algorand.co/metrics portal
    republishes under "Node count". Cached in Redis for one hour so every
    backend worker shares a single fetch.
    """
    try:
        return cached_json(
            _NODELY_CACHE_KEY,
            _NODELY_CACHE_TTL,
            lambda: _fetch_nodely_node_stats_uncached(timeout=timeout),
        )
    except Exception as exc:
        logger.warning("Nodely node stats fetch failed: %s", exc)
        return {}
