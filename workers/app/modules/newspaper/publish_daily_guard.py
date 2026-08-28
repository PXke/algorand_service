"""Atomic Redis-backed daily publish-slot reservation, the single counting authority."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core import config
from app.core.redis_client import get_redis
from app.modules.newspaper.publish_policy import PublishTier

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)


def _client() -> redis.Redis:
    return get_redis()


def _day_key(when: datetime | None = None) -> str:
    moment = when or datetime.now(tz=UTC)
    return moment.strftime("%Y-%m-%d")


def _counter_key(*, tier: PublishTier, day: str) -> str:
    return f"news:publish_count:{tier.value}:{day}"


def _init_key(*, tier: PublishTier, day: str) -> str:
    return f"news:publish_count:init:{tier.value}:{day}"


def _cap_for_tier(tier: PublishTier) -> int:  # noqa: ARG001 -- tier kept for API stability, see PublishTier's docstring
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

    from app.modules.newspaper.article_store import count_articles_published_on_utc_day
    from app.modules.newspaper.publish_policy import utc_day_start_epoch

    day_start = utc_day_start_epoch(when)
    db_count = count_articles_published_on_utc_day(day_start_epoch=day_start)

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


# --------------------------------------------------------------------------- #
# Daily lane tracking (Lane 1 human / Lane 2 scale / Lane 3 discovery) — one
# of the standard-tier daily slots per lane, mirroring the counter keys above.
# --------------------------------------------------------------------------- #
def _lanes_key(day: str) -> str:
    return f"news:publish_lanes_used:{day}"


def lanes_used_today() -> set[str]:
    """Which of today's lanes have already consumed their slot.

    Lanes are "human", "scale", "discovery" — drain_standard_publish_queue
    checks this before picking a lane for the next run.

    Fails open (empty set, i.e. "no lane used yet") on a Redis blip — the
    standard-publish drain must never stop composing entirely just because
    lane bookkeeping is momentarily unreachable; worst case a lane's slot
    gets picked again by the plain-priority fallback for this one run.
    """
    try:
        client = _client()
        raw = client.smembers(_lanes_key(_day_key()))
        return set(raw)
    except Exception:
        logger.warning(
            "lanes_used_today: Redis unavailable, treating as no lanes used", exc_info=True
        )
        return set()


def record_lane_used(lane: str) -> None:
    """Mark one lane as consumed for today.

    Same TTL as the publish counters (expires well past day-end, cheap to
    let it linger). Best-effort: a Redis blip here must not fail an
    otherwise-successful compose+publish.
    """
    try:
        day = _day_key()
        key = _lanes_key(day)
        client = _client()
        client.sadd(key, lane)
        client.expire(key, 90_000)
    except Exception:
        logger.warning(
            "record_lane_used(%s): Redis unavailable, lane not recorded", lane, exc_info=True
        )
