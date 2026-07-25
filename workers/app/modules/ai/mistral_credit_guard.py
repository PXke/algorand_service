"""Circuit breaker for Mistral credit exhaustion, coordinated across all Celery worker processes via Redis.

Why: Mistral's prepaid credit resets on the 1st of the month. When it runs
out mid-month (or the key gets revoked), every Mistral call fails with
401/402 (MistralCreditError) -- but nothing remembered that between calls, so
every compose flow still attempted the full request before finding out.
drain_standard_publish_queue re-walked its whole queue and re-hit the dead
key every beat (hourly) for as long as the outage lasted (observed: 17+
hours, 2026-07-23/24), each row paying a real HTTP round trip (plus a second
one for the model-metadata prefetch) just to fail the same way again.

This flag is set the first time a MistralCreditError is seen, with a TTL to
the next month's credit reset, so every later call in the outage window
fails fast -- no HTTP round trip -- instead of repeating the request/retry
cycle. To resume before the natural reset (credits topped up, key rotated
mid-month), clear it by hand: ``redis-cli DEL mistral:credit_exhausted``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.config import REDIS_URL

logger = logging.getLogger(__name__)

_KEY = "mistral:credit_exhausted"


def _seconds_until_next_month_utc(now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    if now.month == 12:
        reset_at = now.replace(
            year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    else:
        reset_at = now.replace(
            month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    return max(1, int((reset_at - now).total_seconds()))


def mark_credit_exhausted() -> None:
    """Set the circuit breaker, TTL'd to the next monthly credit reset.

    Best-effort: a Redis failure here just means the next call finds out the
    slow way (a real 401), never blocks anything.
    """
    try:
        import redis

        client = redis.from_url(REDIS_URL, decode_responses=True)
        client.set(_KEY, "1", ex=_seconds_until_next_month_utc())
        logger.warning("Mistral credit exhausted — short-circuiting further calls until reset")
    except Exception:
        logger.warning("failed to set mistral credit-exhausted flag", exc_info=True)


def is_credit_exhausted() -> bool:
    """True if a prior call already confirmed Mistral credit is exhausted this cycle. Fails open (False) on any Redis error -- never blocks real work on a cache outage."""
    try:
        import redis

        client = redis.from_url(REDIS_URL, decode_responses=True)
        return bool(client.get(_KEY))
    except Exception:
        return False
