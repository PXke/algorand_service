"""Per-article read tally (Cassandra counter, fed by a Redis pending-increment buffer).

Decoupled from the ArticleStore Protocol: a no-op when the API runs the
in-memory store (dev/tests), so endpoints can always call record_view safely.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.config import settings

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

# Namespaced to match this codebase's other "news:"-prefixed Redis keys
# (publish_schedule.py's news:last_standard_publish_epoch,
# publish_daily_guard.py's news:publish_count:...) -- distinct from backend's
# "algorand:cache:" JSON-cache prefix (app/core/cache.py) and the feed cache's
# "algorand:cache:news:feed:first:*" keys, so a SCAN for either can't collide
# with the other.
VIEW_PENDING_PREFIX = "news:views:pending:"


@lru_cache(maxsize=1)
def _redis_client() -> redis.Redis:
    import redis

    return redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)


def _cassandra_enabled() -> bool:
    return settings.news_store.strip().lower() == "cassandra"


def _as_uuid(article_id: str) -> UUID | None:
    try:
        return UUID(article_id)
    except (ValueError, TypeError):
        return None


def record_view(article_id: str) -> None:
    """Best-effort +1; never raises into the request path.

    Increments a pending-count key in Redis instead of writing Cassandra's
    article_view_counts counter table directly (2026-08-25): counter-column
    writes are Cassandra's own separate write path (can't batch with normal
    writes) and get worse as the cluster grows, so the hot article-page-view
    path no longer touches Cassandra at all. A workers/ Celery beat task
    (flush_pending_views, every 10 minutes) drains these Redis increments
    into the real counter -- see workers/app/modules/newspaper/view_counts.py.
    Both services read the same REDIS_URL, so a key written here is visible
    to that job.
    """
    if not _cassandra_enabled():
        return
    aid = _as_uuid(article_id)
    if aid is None:
        return
    try:
        _redis_client().incr(VIEW_PENDING_PREFIX + str(aid))
    except Exception:
        logger.warning("failed to record pending view for article %s", article_id, exc_info=True)


def get_views_bulk(article_ids: list[str]) -> dict[str, int]:
    """Read tallies for many articles in one parallel sweep. Missing counters (never-viewed articles) and any Cassandra hiccup read as 0 — ranking is a best-effort view, never an error source."""
    if not _cassandra_enabled() or not article_ids:
        return {}
    pairs = [(raw, aid) for raw in article_ids if (aid := _as_uuid(raw)) is not None]
    if not pairs:
        return {}
    try:
        from app.core.cassandra import execute_parallel_with_args
        from app.core.statements import ViewCountStmts

        results = execute_parallel_with_args(
            ViewCountStmts.GET, [(aid,) for _, aid in pairs], raise_on_error=False
        )
    except Exception:
        logger.warning("bulk view-count read failed", exc_info=True)
        return {}
    counts: dict[str, int] = {}
    for (raw, _), (ok, result) in zip(pairs, results, strict=True):
        if not ok:
            continue
        row = result.one() if hasattr(result, "one") else None
        if row is not None and row.views is not None:
            counts[raw] = int(row.views)
    return counts


def get_views(article_id: str) -> int:
    """Return the stored view count for article_id, or 0 if unavailable."""
    if not _cassandra_enabled():
        return 0
    aid = _as_uuid(article_id)
    if aid is None:
        return 0
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ViewCountStmts

        row = get_cassandra_session().execute(ViewCountStmts.GET, (aid,)).one()
    except Exception:
        return 0
    return int(row.views) if row and row.views is not None else 0
