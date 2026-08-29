"""Shared Redis incr-and-expire counter.

The primitive behind every per-key rate limit in this codebase. Five separate copies of "incr key, set expire on the first hit, compare
against a limit" existed before this (auth/session_store.py,
seo/analytics_store.py, contact/api/routes.py, sharing/api/routes.py,
x402_directory/services/rate_limit.py) -- but they are not drop-in
identical: fail-open vs fail-closed on a Redis error is a deliberate,
security-reasoned choice per caller (sharing's comment endpoint fails
*closed* specifically because a leaked share token has no wallet/session
behind it -- see its own comment), and callers disagree on whether they want
a boolean or the raw count back (seo's frequency check wants the count
itself, not a threshold decision). So this factors out only the actual
duplicated mechanism -- the counter -- and leaves the fail-open/closed
policy and the boolean-vs-count interpretation to the caller, rather than
forcing one behaviour on all five.

Existing callers are deliberately NOT migrated here (same convention as
redis_client.py's own docstring) -- this is the primitive new code (starting
with x402_directory) reaches for, not a refactor of the ones that already
work and are already tested.
"""

from __future__ import annotations

import logging

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)


def incr_with_expiry(key: str, *, window_seconds: int) -> int | None:
    """Increment the counter at `key`, setting its expiry on the first hit.

    Returns the new count, or None if Redis itself failed -- callers decide
    what None means for them (fail open or fail closed), this function never
    decides that on their behalf.
    """
    try:
        client = get_redis()
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, window_seconds)
        return count
    except Exception:
        logger.warning("rate-limit counter check failed for key=%s", key, exc_info=True)
        return None
