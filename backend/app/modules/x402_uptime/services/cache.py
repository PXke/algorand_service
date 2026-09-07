"""Redis cache for uptime check results, keyed by normalized target URL.

One key per target holds the LAST result regardless of freshness -- "does
this key still exist" (governed by a fixed, generous Redis TTL) and "is it
still fresh enough to serve as a cache hit" (governed by
x402_uptime_cache_ttl_up_seconds / ..._down_seconds, checked at read time
against the stored `cached_at`) are deliberately two separate questions, not
one Redis-native expiry. The second question needing the outcome
(reachable/http_status) to answer is exactly why an "up" result and a "down"
result get different freshness windows -- see
docs/x402-uptime-check-design.md's Caching section for the full reasoning.

Every accessor fails soft: a Redis error reading the cache is treated as "no
cached value" (falls through to a real fetch, same fail-open default this
codebase's free-endpoint rate limiters use for the identical reason -- one
Redis blip must not take the product down), and a Redis error writing the
cache is swallowed (the result was already computed and paid for; failing to
cache it costs nothing but a future cache miss).
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field

from app.core import serialization
from app.core.config import settings
from app.core.redis_client import get_redis
from app.modules.x402_uptime.services.checker import UptimeResult

logger = logging.getLogger(__name__)

_KEY_PREFIX = "algorand:x402:uptime:cache:"
# Pure storage lifetime for the raw cached blob -- long enough that the
# stale-fallback path (see rate_limit.py) has something to serve even after
# a target's per-hour real-fetch budget stays exhausted for a while. NOT a
# freshness signal; see module docstring.
_STORAGE_TTL_SECONDS = 86_400


@dataclass(frozen=True)
class CachedCheck:
    """A stored UptimeResult plus the epoch-seconds timestamp it was cached at.

    `latency_samples_ms` is every sample `services/percentiles.sample_target`
    collected for the real check that produced this cache entry (see
    `set_cached`) -- kept alongside the single `result` so a later cache HIT
    or stale-serve can still answer with real percentiles (`compute_percentiles`)
    from the ORIGINAL measurement, instead of only the first sample. Defaults
    to empty for a pre-percentiles cache entry read back after a deploy;
    `get_cached` fills that gap with a single-sample list (see its docstring).
    """

    result: UptimeResult
    cached_at: float  # time.time() epoch seconds
    latency_samples_ms: list[int] = field(default_factory=list)


def cache_key(normalized_url: str) -> str:
    """The Redis key one normalized target URL's cached result is stored under."""
    digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
    return f"{_KEY_PREFIX}{digest[:16]}"


def get_cached(normalized_url: str) -> CachedCheck | None:
    """The last cached result for this target, regardless of freshness, or None."""
    try:
        raw = get_redis().get(cache_key(normalized_url))
    except Exception:
        logger.warning(
            "x402 uptime cache: read failed for %s, treating as a miss",
            normalized_url,
            exc_info=True,
        )
        return None
    if not raw:
        return None
    try:
        payload = serialization.loads(raw)
        result = UptimeResult(
            final_url=payload["final_url"],
            reachable=payload["reachable"],
            http_status=payload["http_status"],
            response_time_ms=payload["response_time_ms"],
            resolved_ip=payload["resolved_ip"],
            error=payload["error"],
            redirect_chain=list(payload["redirect_chain"]),
        )
        raw_samples = payload.get("latency_samples_ms")
        latency_samples_ms = (
            [int(v) for v in raw_samples]
            if isinstance(raw_samples, list) and raw_samples
            # A pre-percentiles cache entry (written before this field
            # existed) or a malformed one -- fall back to a single-sample
            # list from the result itself so compute_percentiles() still
            # gets a real (if minimal) input rather than an empty one.
            else [int(payload["response_time_ms"])]
        )
        return CachedCheck(
            result=result,
            cached_at=float(payload["cached_at"]),
            latency_samples_ms=latency_samples_ms,
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("x402 uptime cache: malformed cache entry for %s", normalized_url)
        return None


def set_cached(
    normalized_url: str, result: UptimeResult, latency_samples_ms: list[int] | None = None
) -> float:
    """Store `result` (plus every percentile sample) as the newest cached value.

    `latency_samples_ms` defaults to a single-sample list built from
    `result` itself, so an existing caller that only ever took one sample
    (or a test) does not need to pass it. Returns the `cached_at` epoch
    seconds used.
    """
    cached_at = time.time()
    samples = list(latency_samples_ms) if latency_samples_ms else [result.response_time_ms]
    payload = {
        "final_url": result.final_url,
        "reachable": result.reachable,
        "http_status": result.http_status,
        "response_time_ms": result.response_time_ms,
        "resolved_ip": result.resolved_ip,
        "error": result.error,
        "redirect_chain": result.redirect_chain,
        "latency_samples_ms": samples,
        "cached_at": cached_at,
    }
    try:
        get_redis().set(
            cache_key(normalized_url), serialization.dumps(payload), ex=_STORAGE_TTL_SECONDS
        )
    except Exception:
        logger.warning(
            "x402 uptime cache: write failed for %s -- result was already computed and paid "
            "for, this only costs a future cache miss",
            normalized_url,
            exc_info=True,
        )
    return cached_at


def is_down_fields(*, reachable: bool, http_status: int) -> bool:
    """The "down" rule as plain fields.

    The single source of truth `is_down` below delegates to, and that
    `services/history_service.py`'s aggregation also uses directly (it
    reads raw Cassandra columns off a StoredUptimeCheck row, not a
    checker.UptimeResult).
    """
    return not reachable or http_status >= 500


def is_down(result: UptimeResult) -> bool:
    """True when a result should use the shorter (down) freshness window."""
    return is_down_fields(reachable=result.reachable, http_status=result.http_status)


def is_fresh(cached: CachedCheck, *, now: float | None = None) -> bool:
    """True when `cached` is still within its outcome-specific freshness window."""
    ttl = (
        settings.x402_uptime_cache_ttl_down_seconds
        if is_down(cached.result)
        else settings.x402_uptime_cache_ttl_up_seconds
    )
    age = (now if now is not None else time.time()) - cached.cached_at
    return age <= ttl


__all__ = [
    "CachedCheck",
    "cache_key",
    "get_cached",
    "is_down",
    "is_down_fields",
    "is_fresh",
    "set_cached",
]
