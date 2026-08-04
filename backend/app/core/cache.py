"""Tiny Redis-backed cache for read-only admin/dashboard aggregates.

These endpoints (training stats, celery overview, domain list) are expensive to
compute but don't need to be real-time — a short TTL collapses repeated loads and
tab switches into a single computation. Fails OPEN: any Redis hiccup just means a
cache miss, never an error, so the dashboard still works if Redis is down.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from functools import lru_cache
from typing import TYPE_CHECKING, TypeVar

from app.core.config import settings

if TYPE_CHECKING:
    import redis

T = TypeVar("T")

_PREFIX = "algorand:cache:"


@lru_cache(maxsize=1)
def _client() -> redis.Redis:
    import redis

    return redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)


@lru_cache(maxsize=1)
def _binary_client() -> redis.Redis:
    import redis

    return redis.from_url(settings.redis_url, decode_responses=False, socket_connect_timeout=2)


def cached_json(key: str, ttl_seconds: int, compute: Callable[[], T]) -> T:
    """Return cached JSON for `key`, else run `compute()`, cache it, and return it.

    `compute`'s result must be JSON-serializable.
    """
    full = _PREFIX + key
    with suppress(Exception):  # cache miss / Redis down → recompute
        hit = _client().get(full)
        if hit is not None:
            return json.loads(hit)
    value = compute()
    with suppress(Exception):
        _client().set(full, json.dumps(value, separators=(",", ":")), ex=ttl_seconds)
    return value


def cached_bytes(key: str, ttl_seconds: int, compute: Callable[[], bytes]) -> bytes:
    """Binary sibling of cached_json — for generated images and other non-JSON payloads (a decode_responses=True client would mangle bytes)."""
    full = _PREFIX + key
    with suppress(Exception):
        hit = _binary_client().get(full)
        if hit is not None:
            return hit
    value = compute()
    with suppress(Exception):
        _binary_client().set(full, value, ex=ttl_seconds)
    return value


def invalidate(*keys: str) -> None:
    """Drop cache entries now (e.g. after a write that changes the aggregate)."""
    if not keys:
        return
    with suppress(Exception):
        _client().delete(*[_PREFIX + k for k in keys])
