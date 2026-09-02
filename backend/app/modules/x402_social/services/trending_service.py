"""Trending topics (post tags) and groups: Redis-only, hourly ZINCRBY buckets with decay-by-age merge on read (design doc sections 2.8-2.9).

**No Cassandra ranking table, ever** (design doc's own words) -- the
features-board lesson generalized: a live-updated rank projection needs
constant delete-then-reinsert maintenance against a counter that changes on
every write, exactly what an atomic counter is supposed to make
unnecessary. Free for the same reason probe history is free (CLAUDE.md
section 9.1 item 7): trending is measurement of public activity, and
charging for it -- or worse, selling influence over it -- would poison the
one neutral discovery signal this marketplace owns.

Fails open to an empty result with a warning log on Redis loss (CLAUDE.md
section 2 invariant 9: "one Redis blip must not crash a beat") -- nothing
durable depends on trending, so losing it is cosmetic.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

_TOPICS_PREFIX = "algorand:x402social:trend:topics:"
_GROUPS_PREFIX = "algorand:x402social:trend:groups:"
_BUCKET_TTL_SECONDS = 48 * 3600
_MERGE_HOURS = 24

# Per-bucket cap on how many (member, score) pairs a single hourly ZSET read
# pulls back (finding 7, 2026-security-audit). Tag/group cardinality is
# attacker-growable -- every settled paid post creates a new potential tag
# member -- so `zrange(key, 0, -1, ...)` (the FULL set) on each of 24 hourly
# buckets, on every free trending read, was an unbounded-scale Redis scan: a
# funded attacker could inflate a bucket's member count arbitrarily and turn
# every subsequent free GET /trending/* into an amplified read against
# Redis. 200 is comfortably more than enough members per bucket to produce a
# correct top-20 final ranking (the largest `_DEFAULT_TRENDING_LIMIT`/
# `x402_social_max_results`-bounded result this module ever returns): each
# bucket is independently truncated to its own top 200 by score, and the
# final merge takes the top N of the union across 24 buckets, so a topic
# that is actually trending is, by construction, always within the top 200
# of whichever bucket(s) it was active in.
_BUCKET_TOP_N = 200

# Weights per design doc section 2.9: a post is the strongest signal, a
# reaction the weakest -- posting costs the most and happens the least
# often, so weighting it highest keeps a single post from being drowned out
# by a burst of cheap reactions on something else.
POST_WEIGHT = 3
COMMENT_WEIGHT = 2
REACTION_WEIGHT = 1


def _bucket_key(prefix: str, *, at: datetime) -> str:
    return f"{prefix}{at.strftime('%Y%m%d%H')}"


def record_topic_activity(tags: list[str], *, weight: int, at: datetime | None = None) -> None:
    """Bump the current hour's bucket for every tag in `tags` by `weight`. Fails open with a warning log on any Redis error."""
    if not tags:
        return
    moment = at or datetime.now(tz=UTC)
    key = _bucket_key(_TOPICS_PREFIX, at=moment)
    try:
        client = get_redis()
        pipe = client.pipeline()
        for tag in tags:
            pipe.zincrby(key, weight, tag)
        pipe.expire(key, _BUCKET_TTL_SECONDS)
        pipe.execute()
    except Exception:
        logger.warning(
            "x402 social trending: topic activity bump failed, continuing", exc_info=True
        )


def record_group_activity(group_id: str, *, weight: int, at: datetime | None = None) -> None:
    """Bump the current hour's bucket for `group_id` by `weight`. Fails open with a warning log on any Redis error."""
    if not group_id:
        return
    moment = at or datetime.now(tz=UTC)
    key = _bucket_key(_GROUPS_PREFIX, at=moment)
    try:
        client = get_redis()
        pipe = client.pipeline()
        pipe.zincrby(key, weight, group_id)
        pipe.expire(key, _BUCKET_TTL_SECONDS)
        pipe.execute()
    except Exception:
        logger.warning(
            "x402 social trending: group activity bump failed, continuing", exc_info=True
        )


def _merge_decayed(
    prefix: str, *, limit: int, now: datetime | None = None
) -> list[tuple[str, float]]:
    """Merge the last _MERGE_HOURS hourly buckets with linear decay by bucket age, return the top `limit` (key, score) pairs, newest-weighted-highest first.

    Decay factor for a bucket `age` hours old (0 = current hour): (_MERGE_HOURS - age) / _MERGE_HOURS
    -- 1.0 for the current hour, sloping linearly to 1/_MERGE_HOURS for the
    oldest bucket in the window (design doc section 2.9: "linear decay by
    bucket age"). Returns an empty list, with a warning log, on any Redis
    error -- fail open, cosmetic loss only (see this module's own
    docstring).

    Each bucket is read with `zrevrange(key, 0, _BUCKET_TOP_N - 1, ...)` --
    the top `_BUCKET_TOP_N` members by score, highest first -- rather than
    the full set (finding 7, 2026-security-audit: see `_BUCKET_TOP_N`'s own
    docstring for why that bound is still exact for the top-N result this
    function ultimately returns).

    All `_MERGE_HOURS` bucket reads are queued on one `client.pipeline()`
    and sent in a single round trip (optimization pass, 2026-09-02) --
    previously each of the 24 `zrevrange` calls was its own blocking
    request, on every free trending read, despite the write side two
    functions above already using a pipeline for the identical reason.
    """
    moment = now or datetime.now(tz=UTC)
    try:
        client = get_redis()
        pipe = client.pipeline()
        keys: list[str] = []
        decays: list[float] = []
        for age in range(_MERGE_HOURS):
            bucket_time = moment.timestamp() - age * 3600
            key = f"{prefix}{datetime.fromtimestamp(bucket_time, tz=UTC).strftime('%Y%m%d%H')}"
            keys.append(key)
            decays.append((_MERGE_HOURS - age) / _MERGE_HOURS)
            pipe.zrevrange(key, 0, _BUCKET_TOP_N - 1, withscores=True)
        bucket_results = pipe.execute()
        totals: dict[str, float] = {}
        for decay, members in zip(decays, bucket_results, strict=True):
            for member, score in members:
                totals[member] = totals.get(member, 0.0) + float(score) * decay
    except Exception:
        logger.warning("x402 social trending: read failed, serving an empty result", exc_info=True)
        return []
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[: max(0, limit)]


def top_topics(*, limit: int, now: datetime | None = None) -> list[tuple[str, float]]:
    """Top `limit` trending tags, highest decayed score first. Empty (with a warning log) on any Redis error."""
    return _merge_decayed(_TOPICS_PREFIX, limit=limit, now=now)


def top_groups(*, limit: int, now: datetime | None = None) -> list[tuple[str, float]]:
    """Top `limit` trending group ids, highest decayed score first. Empty (with a warning log) on any Redis error."""
    return _merge_decayed(_GROUPS_PREFIX, limit=limit, now=now)
