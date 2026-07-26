"""Live algod node status and Nodely node-count telemetry for network tiles."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.cache import cached_json
from app.core.config import settings

logger = logging.getLogger(__name__)

_NODELY_DS_QUERY = "https://g.nodely.io/api/ds/query"
_NODELY_CH_UID = "fc25640e-50ee-4e04-aad6-2a5336c09eaf"
# Shared Redis TTL — at most one Nodely pull per hour across all workers.
_NODELY_CACHE_KEY = "metrics:nodely-validators"
_NODELY_CACHE_TTL = 3600


def fetch_algod_status(*, timeout: float = 8.0) -> dict[str, Any]:
    """Best-effort Algod /v2/status for chain activity tiles."""
    url = settings.algod_url.rstrip("/") + "/v2/status"
    headers: dict[str, str] = {}
    token = settings.algod_token.strip()
    if token:
        headers["X-Algo-API-Token"] = token

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return {}
            return payload
    except Exception as exc:
        logger.warning("Algod status fetch failed: %s", exc)
        return {}


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
    with httpx.Client(timeout=timeout) as client:
        response = client.post(_NODELY_DS_QUERY, json=body)
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

    Off-chain telemetry — the ledger does not expose a validator/node count.
    Cached in Redis for one hour so every backend worker shares a single fetch.
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
