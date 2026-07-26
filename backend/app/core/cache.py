"""Tiny Redis-backed cache for read-only admin/dashboard aggregates.

These endpoints (training stats, celery overview, domain list) are expensive to
compute but don't need to be real-time — a short TTL collapses repeated loads and
tab switches into a single computation. Fails OPEN: any Redis hiccup just means a
cache miss, never an error, so the dashboard still works if Redis is down.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from functools import lru_cache
from typing import TYPE_CHECKING, TypeVar

from app.core.config import settings

if TYPE_CHECKING:
    import redis
    import redis.asyncio

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


@lru_cache(maxsize=1)
def _async_client() -> redis.asyncio.Redis:
    """Process-wide asyncio Redis client.

    Unlike the Cassandra driver, redis-py ships a genuinely awaitable client:
    `redis.asyncio` connects with `asyncio.open_connection` and its
    `execute_command` is a real coroutine, so `await client.get(...)` suspends
    the coroutine and lets the event loop serve other requests -- no thread and
    no future-to-awaitable bridge needed.

    Safe to cache for the life of the process: an asyncio client binds to the
    loop it is used from, and Robyn runs exactly ONE loop per process (verified:
    every request reports the same loop id on MainThread even at workers=8, which
    sizes the Rust-side I/O threads and the sync-handler pool, not Python loops).
    """
    import redis.asyncio

    return redis.asyncio.from_url(
        settings.redis_url, decode_responses=True, socket_connect_timeout=2
    )


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


async def cached_json_await(
    key: str,
    ttl_seconds: int,
    compute: Callable[[], T] | Callable[[], Awaitable[T]],
) -> T:
    """Awaitable `cached_json`: the Redis GET/SET yield instead of blocking the loop.

    Same fail-open contract as the sync version -- any Redis hiccup degrades to a
    recompute, never an error.

    `compute` may be sync or async, deliberately: the request paths are being
    converted to async one at a time, and accepting both means a caller can move
    its Redis round-trips off the event loop before its whole query chain is
    awaitable. A sync `compute` still blocks the loop while it runs, so it is a
    way-station, not a destination.
    """
    full = _PREFIX + key
    client = _async_client()
    with suppress(Exception):  # cache miss / Redis down -> recompute
        hit = await client.get(full)
        if hit is not None:
            return json.loads(hit)
    value = compute()
    if inspect.isawaitable(value):
        value = await value
    with suppress(Exception):
        await client.set(full, json.dumps(value, separators=(",", ":")), ex=ttl_seconds)
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
