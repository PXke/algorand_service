"""Per-resource circuit breaker for the auto-refund path (modules/x402/paid_request.run_with_refund).

Every refund costs a real Algorand transaction fee on top of the refunded
amount, so a bug in one endpoint's product write -- or an adversary
deliberately triggering failures -- could cheaply drain the refund wallet
one failed call + one refund-tx-fee at a time (owner requirement
2026-09-02). A resource that crosses `x402_refund_breaker_max_failures`
refund-triggering failures within `x402_refund_breaker_window_seconds` trips:
callers must check `is_tripped(resource)` and refuse the request BEFORE it
ever reaches the payment gate, so no further money is ever at risk while a
resource is tripped.

Two separate Redis keys per resource, not one:

* `refund_breaker:<resource>` -- the rolling failure COUNTER, TTL'd to the
  configured window (via the shared `incr_with_expiry`). This alone is a
  rate limit, not a latch: once the TTL expires the counter silently reads
  back to zero and the breaker un-trips itself with no admin involved.
* `refund_breaker:latched:<resource>` -- set with NO TTL the moment the
  counter first crosses the threshold, and the actual thing `is_tripped`
  checks. Only `reset()` (the admin route) clears it. This is the fix for a
  real, found-in-audit gap (2026-09-02): the counter-only version let an
  attacker trigger 5 refunds, wait out the 10-minute window, and repeat
  indefinitely -- "trips until an admin resets it" was the documented
  contract but was not what the code actually did.

Deliberately FAILS CLOSED on a Redis outage, unlike this codebase's usual
free-endpoint rate limits (app.core.rate_limit.incr_with_expiry, which fail
open so a Redis blip does not take a free surface offline). This guards real
money leaving a hot wallet, so "we could not verify the breaker is closed"
must block, not silently allow -- the same fail-closed exception this
codebase already makes for promo.py's redemption caps and the scan
endpoint's concurrency limiter, for the identical reason.
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.core.rate_limit import incr_with_expiry
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

_TRIP_KEY_PREFIX = "algorand:x402:refund_breaker:"
_LATCH_KEY_PREFIX = "algorand:x402:refund_breaker:latched:"


def _set_latch(resource: str) -> None:
    """Best-effort: set the no-TTL latch. A failure here just means is_tripped's own fail-closed path covers the gap on the next check."""
    try:
        get_redis().set(f"{_LATCH_KEY_PREFIX}{resource}", "1")
    except Exception:
        logger.error(
            "x402 refund circuit breaker: failed to set the trip latch for resource=%s -- "
            "the rolling counter will still cause is_tripped to fail closed if Redis is "
            "unreachable, but the latch itself did not persist",
            resource,
            exc_info=True,
        )


def record_refund_failure(resource: str) -> int | None:
    """Count one refund-triggering failure for `resource` in the rolling window, and latch the breaker the moment the count crosses the threshold.

    Reuses the shared incr-and-expire primitive every other budget in this
    codebase uses for the counter itself. Returns the new count, or None if
    Redis itself failed -- best-effort: a failure to COUNT a failure must
    not itself raise and block the caller's own error handling.
    """
    count = incr_with_expiry(
        f"{_TRIP_KEY_PREFIX}{resource}",
        window_seconds=settings.x402_refund_breaker_window_seconds,
    )
    if count is not None and count >= settings.x402_refund_breaker_max_failures:
        _set_latch(resource)
    return count


def is_tripped(resource: str) -> bool:
    """True when `resource` must be refused BEFORE the payment gate.

    Checks the no-TTL latch first -- that is the actual trip state, and it
    never silently expires. Also true if Redis itself could not be reached
    at all -- see this module's own docstring for why an unreachable
    breaker fails CLOSED here, unlike most budgets in this codebase. A plain
    read (GET), never incr_with_expiry: checking must never itself count as
    a failure.
    """
    try:
        client = get_redis()
        if client.get(f"{_LATCH_KEY_PREFIX}{resource}") is not None:
            logger.error(
                "x402 refund circuit breaker TRIPPED (latched) for resource=%s -- refusing "
                "until an admin resets it",
                resource,
            )
            return True
        raw = client.get(f"{_TRIP_KEY_PREFIX}{resource}")
    except Exception:
        logger.error(
            "x402 refund circuit breaker: could not verify trip state for resource=%s -- "
            "failing CLOSED (refusing the request)",
            resource,
            exc_info=True,
        )
        return True
    count = int(raw) if raw is not None else 0
    tripped = count >= settings.x402_refund_breaker_max_failures
    if tripped:
        # The counter crossed the threshold but the latch write in
        # record_refund_failure apparently didn't land (or hasn't been
        # read back yet) -- still refuse, and try to latch it now so the
        # NEXT check does not depend on this same race.
        logger.error(
            "x402 refund circuit breaker TRIPPED for resource=%s: %d refund-triggering "
            "failures within the last %ds window (threshold %d) -- latch was missing, "
            "setting it now",
            resource,
            count,
            settings.x402_refund_breaker_window_seconds,
            settings.x402_refund_breaker_max_failures,
        )
        _set_latch(resource)
    return tripped


def reset(resource: str) -> bool:
    """Admin action: clear both the counter and the latch for `resource`. Returns True if either key actually existed."""
    try:
        client = get_redis()
        deleted_trip = client.delete(f"{_TRIP_KEY_PREFIX}{resource}")
        deleted_latch = client.delete(f"{_LATCH_KEY_PREFIX}{resource}")
        return bool(deleted_trip or deleted_latch)
    except Exception:
        logger.exception("x402 refund circuit breaker: reset failed for resource=%s", resource)
        return False
