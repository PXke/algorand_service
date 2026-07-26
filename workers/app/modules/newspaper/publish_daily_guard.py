"""Atomic Redis-backed daily publish-slot reservation, the single counting authority."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import redis

from app.core import config
from app.modules.newspaper.publish_policy import PublishTier

logger = logging.getLogger(__name__)


def _client() -> redis.Redis:
    from app.core.config import REDIS_URL

    return redis.from_url(REDIS_URL, decode_responses=True)


def _day_key(when: datetime | None = None) -> str:
    moment = when or datetime.now(tz=UTC)
    return moment.strftime("%Y-%m-%d")


def _counter_key(*, tier: PublishTier, day: str) -> str:
    return f"news:publish_count:{tier.value}:{day}"


def _init_key(*, tier: PublishTier, day: str) -> str:
    return f"news:publish_count:init:{tier.value}:{day}"


def _cap_for_tier(tier: PublishTier) -> int:
    if tier == PublishTier.BREAKING:
        return config.NEWS_MAX_BREAKING_PER_DAY
    return config.NEWS_MAX_ARTICLES_PER_DAY


def published_count_today(*, tier: PublishTier, when: datetime | None = None) -> int:
    """Current reserved publish count for UTC day (Redis)."""
    day = _day_key(when)
    _ensure_counter_initialized(tier=tier, day=day, when=when)
    raw = _client().get(_counter_key(tier=tier, day=day))
    return int(raw) if raw else 0


def _ensure_counter_initialized(
    *,
    tier: PublishTier,
    day: str,
    when: datetime | None = None,
) -> None:
    client = _client()
    init = _init_key(tier=tier, day=day)
    if not client.set(init, "1", nx=True, ex=90_000):
        return

    from app.modules.newspaper.publish_policy import (
        count_breaking_articles_on_utc_day,
        count_standard_articles_on_utc_day,
        utc_day_start_epoch,
    )

    day_start = utc_day_start_epoch(when)
    if tier == PublishTier.BREAKING:
        db_count = count_breaking_articles_on_utc_day(day_start_epoch=day_start)
    else:
        db_count = count_standard_articles_on_utc_day(day_start_epoch=day_start)

    key = _counter_key(tier=tier, day=day)
    client.set(key, db_count, ex=90_000)
    logger.info("initialized publish counter %s=%s from feed", key, db_count)


def reserve_publish_slot(*, tier: PublishTier) -> tuple[bool, str]:
    """Atomically reserve one publish slot for today.

    Must call release_publish_slot if compose/insert fails after reserve.
    """
    day = _day_key()
    _ensure_counter_initialized(tier=tier, day=day)
    cap = _cap_for_tier(tier)
    key = _counter_key(tier=tier, day=day)
    client = _client()
    count = int(client.incr(key))
    client.expire(key, 90_000)

    if count > cap:
        client.decr(key)
        logger.warning(
            "publish cap blocked tier=%s count would be %s cap=%s",
            tier.value,
            count,
            cap,
        )
        return False, f"{tier.value}_daily_cap_hard_limit ({cap})"

    return True, "ok"


def release_publish_slot(*, tier: PublishTier) -> None:
    """Rollback reservation when insert did not happen."""
    day = _day_key()
    key = _counter_key(tier=tier, day=day)
    client = _client()
    if int(client.get(key) or 0) > 0:
        client.decr(key)


def is_standard_publish_saturated(*, when: datetime | None = None) -> bool:
    """Whether today's standard-tier publish cap has been reached."""
    count = published_count_today(tier=PublishTier.STANDARD, when=when)
    return count >= config.NEWS_MAX_ARTICLES_PER_DAY


def assert_publish_allowed(*, tier: PublishTier) -> None:
    """Raise if cap already reached (pre-check before reserve)."""
    cap = _cap_for_tier(tier)
    current = published_count_today(tier=tier)
    if current >= cap:
        msg = f"{tier.value} daily publish cap reached ({current}/{cap})"
        raise PublishCapExceededError(msg)


class PublishCapExceededError(Exception):
    """Raised when a publish-slot reservation exceeds the daily cap."""
    pass
