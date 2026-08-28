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

DeepSeek (pay-as-you-go, added later) shares this same breaker but with its
own TTL (1h, not "until next month reset" -- see `_ttl_seconds`) and only
trips on a 402 (Payment Required); a DeepSeek 401 is treated as an auth
problem, not billing, and never trips the breaker (see
`mark_credit_exhausted`'s `status_code` param).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.config import REDIS_URL

logger = logging.getLogger(__name__)


def _key(provider: str) -> str:
    return f"{provider}:credit_exhausted"


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


# DeepSeek is pay-as-you-go, not a monthly prepaid credit like Mistral -- a
# genuine billing block there clears in minutes to hours once topped up, not
# "wait for the 1st of next month." Reusing Mistral's until-next-month TTL
# for DeepSeek would silently short-circuit the whole platform for up to a
# month after a same-day top-up. Mistral keeps its existing TTL, unchanged --
# it really is a monthly-reset prepaid balance.
_DEEPSEEK_BREAKER_TTL_SECONDS = 3600


def _ttl_seconds(provider: str, now: datetime | None = None) -> int:
    """Breaker TTL for `provider`: DeepSeek gets a flat 1h; every other provider routed through this guard (Mistral, and anything else) keeps the until-next-month-reset TTL, unchanged."""
    if provider == "deepseek":
        return _DEEPSEEK_BREAKER_TTL_SECONDS
    return _seconds_until_next_month_utc(now)


def mark_credit_exhausted(provider: str = "mistral", *, status_code: int | None = None) -> None:
    """Set the circuit breaker for `provider`, TTL'd per `_ttl_seconds`.

    Keyed per-provider (not one global flag) since adding a second provider
    (DeepSeek) means a dead key on one must never short-circuit the other's
    calls too.

    `status_code`, when given, lets a DeepSeek 401 skip tripping the breaker
    entirely: DeepSeek's pay-as-you-go billing block is a 402 (Payment
    Required); a 401 there is an auth problem (bad/rotated key) -- a
    different failure mode that an hour-long platform-wide short-circuit
    doesn't fix and shouldn't be blamed on. Mistral's own history
    (2026-07-23/24) is the opposite -- its real credit exhaustion showed up
    AS a 401 there -- so Mistral keeps tripping on both codes, unchanged.

    Best-effort: a Redis failure here just means the next call finds out the
    slow way (a real 401/402), never blocks anything.
    """
    if provider == "deepseek" and status_code == 401:
        logger.warning(
            "deepseek returned 401 (not 402) -- treating as an auth problem, "
            "not credit exhaustion; not tripping the breaker"
        )
        return
    try:
        import redis

        client = redis.from_url(REDIS_URL, decode_responses=True)
        client.set(_key(provider), "1", ex=_ttl_seconds(provider))
        logger.warning(
            "%s credit exhausted — short-circuiting further calls until reset", provider
        )
    except Exception:
        logger.warning("failed to set %s credit-exhausted flag", provider, exc_info=True)


def is_credit_exhausted(provider: str = "mistral") -> bool:
    """True if a prior call already confirmed `provider`'s credit is exhausted this cycle. Fails open (False) on any Redis error -- never blocks real work on a cache outage."""
    try:
        import redis

        client = redis.from_url(REDIS_URL, decode_responses=True)
        return bool(client.get(_key(provider)))
    except Exception:
        return False
