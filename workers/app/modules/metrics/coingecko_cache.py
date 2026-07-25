"""Redis caching for CoinGecko so we don't hammer their API.

The collector (hourly), the weekly digest and the price-analysis path all hit
CoinGecko independently; without caching they make duplicate calls and a transient
429 breaks them. This module caches each response briefly, caches the (static)
asset name for a week (so we skip the heavy /coins/{id} call), and keeps a
last-good copy to serve when CoinGecko errors. All helpers fail soft.
"""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis


def _client() -> redis.Redis:
    import redis

    from app.core.config import REDIS_URL

    return redis.from_url(REDIS_URL, decode_responses=True)


def get_json(key: str) -> dict[str, Any] | None:
    """Fetch and parse a cached JSON value, returning None on any failure."""
    try:
        raw = _client().get(key)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def set_json(key: str, value: dict[str, Any], ttl: int) -> None:
    """Cache a JSON-serializable value under key for ttl seconds, failing soft."""
    with contextlib.suppress(Exception):
        _client().set(key, json.dumps(value), ex=ttl)


def get_name(asset_id: str) -> str | None:
    """Fetch a cached asset display name, or None if uncached or on error."""
    try:
        return _client().get(f"coingecko:name:{asset_id}")
    except Exception:
        return None


def set_name(asset_id: str, name: str) -> None:
    """Cache an asset's display name for COINGECKO_NAME_TTL seconds."""
    from app.core.config import COINGECKO_NAME_TTL

    with contextlib.suppress(Exception):
        _client().set(f"coingecko:name:{asset_id}", name, ex=COINGECKO_NAME_TTL)
