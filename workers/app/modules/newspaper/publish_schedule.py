from __future__ import annotations

import time

import redis

from app.core.config import NEWS_STANDARD_INTERVAL_HOURS, REDIS_URL

_REDIS_KEY_LAST_STANDARD = "news:last_standard_publish_epoch"


def _redis_client() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def last_standard_publish_epoch() -> int | None:
    raw = _redis_client().get(_REDIS_KEY_LAST_STANDARD)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def record_standard_publish(*, epoch: int | None = None) -> None:
    moment = epoch if epoch is not None else int(time.time())
    _redis_client().set(_REDIS_KEY_LAST_STANDARD, str(moment))


def standard_publish_interval_seconds() -> int:
    return max(1, NEWS_STANDARD_INTERVAL_HOURS) * 3600


def is_standard_publish_due(*, now_epoch: int | None = None) -> tuple[bool, str]:
    """True when a scheduled (non-breaking) article may leave the queue."""
    now = now_epoch if now_epoch is not None else int(time.time())
    last = last_standard_publish_epoch()
    if last is None:
        return True, "no_prior_standard_publish"
    elapsed = now - last
    interval = standard_publish_interval_seconds()
    if elapsed >= interval:
        return True, f"interval_elapsed ({elapsed}s >= {interval}s)"
    return False, f"wait_standard_interval ({interval - elapsed}s remaining)"

