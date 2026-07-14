from __future__ import annotations

import logging
import time

import redis

from app.core.config import NEWS_STANDARD_INTERVAL_HOURS, REDIS_URL

logger = logging.getLogger(__name__)

_REDIS_KEY_LAST_STANDARD = "news:last_standard_publish_epoch"


def _redis_client() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def last_standard_publish_epoch() -> int | None:
    try:
        raw = _redis_client().get(_REDIS_KEY_LAST_STANDARD)
    except Exception:
        logger.warning("failed to read last standard-publish epoch", exc_info=True)
        raise
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def record_standard_publish(*, epoch: int | None = None) -> None:
    moment = epoch if epoch is not None else int(time.time())
    try:
        _redis_client().set(_REDIS_KEY_LAST_STANDARD, str(moment))
    except Exception:
        # The article is already committed to the feed by the time this is
        # called — don't let a Redis error surface as a task failure for
        # already-successful work. The pacing clock simply won't advance this
        # once; the next due-check re-reads Redis fresh next run.
        logger.warning("failed to record standard-publish epoch", exc_info=True)


def standard_publish_interval_seconds() -> int:
    return max(1, NEWS_STANDARD_INTERVAL_HOURS) * 3600


def is_standard_publish_due(*, now_epoch: int | None = None) -> tuple[bool, str]:
    """True when a scheduled (non-breaking) article may leave the queue.

    Fails CLOSED: a Redis error must never look like "clock elapsed, go ahead
    and publish" — that would silently bypass the pacing cadence entirely.
    Skipping this run and retrying later is the safe direction (matches the
    backend's AdminCassandraStore._is_standard_publish_due, which fails the
    same way for the admin-approve release path)."""
    now = now_epoch if now_epoch is not None else int(time.time())
    try:
        last = last_standard_publish_epoch()
    except Exception:
        return False, "redis_error"
    if last is None:
        return True, "no_prior_standard_publish"
    elapsed = now - last
    interval = standard_publish_interval_seconds()
    if elapsed >= interval:
        return True, f"interval_elapsed ({elapsed}s >= {interval}s)"
    return False, f"wait_standard_interval ({interval - elapsed}s remaining)"

