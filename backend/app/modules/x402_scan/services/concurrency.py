"""Host-wide concurrency cap on in-flight scans -- the fix for the security audit's #1 finding.

Every accepted request spins a --memory 1536m --cpus 1 container and streams
up to x402_scan_max_download_bytes (1GB) to disk (see sandbox_runner.py /
scan_service.py). There is no separate host for this sandbox (owner
decision, single shared box with the newspaper pipeline and payment
settlement) -- N concurrent payers means N x that resource cost with nothing
else bounding N. This caps it.

Deliberately FAILS CLOSED on a Redis error, unlike most cooldown/lock checks
in this codebase (CLAUDE.md invariant 9 default is fail-open, "one Redis
blip must not crash a beat"). That default exists for scheduling beats where
the failure mode of "skip this cycle" is cheap. Here the failure mode of
failing open is "allow unlimited concurrent 1.5GB containers on a shared
prod host" -- exactly the DoS this module exists to prevent -- so a Redis
outage must reject new scans (503), not remove the only cap. This mirrors
established precedent in this codebase for per-caller fail-closed judgment
(see core/rate_limit.py's own docstring, citing sharing/api/routes.py's
comment endpoint).

Uses a plain INCR/DECR counter, not a fully crash-proof sorted-set of
per-request expiring slots -- release() is called in a `finally` by every
caller, and the key's own TTL (refreshed only on the first acquire) serves
as a coarse self-heal if a process ever crashes between acquire and release
without a chance to run `finally`. Good enough for a v0 prototype gating a
disabled-by-default endpoint; a sorted-set with per-slot expiry is the more
precise v2 version if this needs tightening.
"""

from __future__ import annotations

import logging

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

_KEY = "algorand:x402:scan_inflight_count"
# Generous upper bound on a single scan's real duration (sandbox timeout is
# 90s, download timeout 60s) -- exists only to self-heal a crash that skips
# release(), not as a normal-path timer.
_SAFETY_TTL_S = 300

MAX_CONCURRENT_SCANS = 4


class ConcurrencyLimitError(Exception):
    """Either the host-wide scan slot limit is full, or the limiter itself is unavailable."""


def acquire_scan_slot() -> None:
    """Reserve one of MAX_CONCURRENT_SCANS slots, or raise ConcurrencyLimitError.

    Raises on: the cap being genuinely full, OR Redis being unreachable
    (fail CLOSED -- see module docstring for why this differs from the
    fail-open default elsewhere in this codebase).
    """
    try:
        client = get_redis()
        count = int(client.incr(_KEY))
        if count == 1:
            client.expire(_KEY, _SAFETY_TTL_S)
    except Exception as exc:
        logger.error("x402 scan concurrency limiter: Redis unreachable, rejecting", exc_info=True)
        raise ConcurrencyLimitError("scan concurrency limiter unavailable") from exc

    if count > MAX_CONCURRENT_SCANS:
        release_scan_slot()
        raise ConcurrencyLimitError(
            f"scan concurrency limit reached ({MAX_CONCURRENT_SCANS} in flight)"
        )


def release_scan_slot() -> None:
    """Release a slot acquired by acquire_scan_slot(). Never raises -- always call from `finally`."""
    try:
        get_redis().decr(_KEY)
    except Exception:
        logger.warning("x402 scan concurrency limiter: failed to release slot", exc_info=True)
