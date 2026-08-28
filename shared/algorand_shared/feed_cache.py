"""Best-effort invalidation of the reader feed's cached first page.

backend's `app.core.cache` (see backend/app/core/cache.py) cache-asides the
reader feed endpoint (`GET /api/v1/news/feed`) keyed by every filter param
(service_id/tag/lang/limit) plus whether a cursor was given -- see
backend/app/modules/news/api/routes.py's `feed()`. A write can only ever
change the *first* page (no cursor) of *some* filter combination: older,
cursor-paginated pages are immutable once cached (nothing about page 3
changes when a new article publishes). Which exact filter/lang combo(s) are
affected depends on the written article's service_id/tags/available
translations -- enumerating all of those here would be more machinery than
this is worth, so this instead SCANs for every cached first-page key (all
filter/lang/limit combos, matching the `news:feed:first:*` key prefix the
route writes) and drops them in one pass. The cache is small (short TTL, low
request volume), so a full SCAN of just its "first page" slice is cheap.

Called from both services' write paths (workers' article_store.py,
shared's transition_article_status below), same pattern as
article_transitions.py using a lazy import so it works no matter which
service's `app.core` package happens to be on the path -- except this reads
REDIS_URL straight from the environment instead of importing either
service's config, since backend exposes it as `settings.redis_url` and
workers as a module-level `REDIS_URL` constant. Both ultimately source the
same `REDIS_URL` env var (confirmed against both services' core/config.py),
so this is a distinction without a difference here.

`_client()` process-caches its Redis client (one per interpreter, same
one-client-per-process idea as backend/app/core/cache.py's `_client()` and
workers/app/core/redis_client.py's `get_redis()`) instead of dialing a new
one on every invalidation -- this module intentionally does NOT import
either service's shared client for that (see the REDIS_URL note above: it
must stay constructible with neither service's `app.core` on the path).
Tests exercising this module directly should monkeypatch `_client` itself,
not `redis.from_url`, since a cached client would otherwise outlive a
per-test fake -- see workers/tests/test_feed_cache_invalidation.py and
backend/tests/test_news_feed_cache.py.

Fails OPEN, like every other best-effort Redis call in this codebase (see
workers/app/modules/newspaper/publish_schedule.py's `record_standard_publish`):
a write is already committed to Cassandra by the time this runs, so a Redis
blip here must never surface as a failure to the caller -- worst case the
stale first page is served until the cache's own 60s TTL expires.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "algorand:cache:"
_FEED_FIRST_PAGE_MATCH = _CACHE_PREFIX + "news:feed:first:*"


@lru_cache(maxsize=1)
def _client() -> redis.Redis:
    import redis

    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(url, decode_responses=True, socket_connect_timeout=2)


def invalidate_feed_first_page() -> None:
    """Drop every cached feed first-page variant. Never raises."""
    try:
        client = _client()
        keys = list(client.scan_iter(match=_FEED_FIRST_PAGE_MATCH, count=200))
        if keys:
            client.delete(*keys)
    except Exception:
        logger.warning("failed to invalidate feed first-page cache", exc_info=True)
