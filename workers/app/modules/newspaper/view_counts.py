"""Worker-side reader (and, since 2026-08-25, flusher) for the per-article read tally.

Shares the article_view_counts counter table (migration 026). Reads
(get_views_bulk) are unchanged. Increments no longer happen directly on
backend's article-detail fetch -- that hot path now only INCRs a per-article
pending key in Redis (app/modules/news/stores/view_counts.py's record_view,
keys prefixed "news:views:pending:"). flush_pending_views drains those into
this counter table on a Celery beat schedule (see celery_app.py), because a
Cassandra COUNTER write is its own separate write path that can't batch with
ordinary writes -- worth avoiding on every single page view, fine to pay once
every 10 minutes for a batch of them.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

# Must match backend's app/modules/news/stores/view_counts.VIEW_PENDING_PREFIX.
VIEW_PENDING_PREFIX = "news:views:pending:"
_VIEW_PENDING_MATCH = VIEW_PENDING_PREFIX + "*"


def _redis_client() -> redis.Redis:
    import redis

    from app.core.config import REDIS_URL

    return redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)


def flush_pending_views() -> dict[str, int]:
    """Drain Redis's pending per-article view increments into the Cassandra counter.

    Runs every 10 minutes as a Celery beat task -- see celery_app.py's
    "flush-pending-view-counts" entry. Trades real-time accuracy for a fixed
    reconciliation window: nothing reads article_view_counts for anything
    exact or sub-10-minute (hot_feed's velocity ranking floors article age at
    6 hours, so a 10-minute lag is noise at that granularity). A worker crash
    mid-window loses at most one window's worth of increments for a
    vanity/ranking metric -- self-correcting on the next real view, not worth
    designing around further.

    Best-effort throughout, matching every other Redis touch in this
    codebase: a Redis outage flushes nothing this cycle (increments stay
    parked for the next run); a per-key Cassandra failure skips that one key
    without losing ones already applied in the same pass.
    """
    try:
        client = _redis_client()
        keys = list(client.scan_iter(match=_VIEW_PENDING_MATCH, count=200))
    except Exception:
        logger.warning("flush_pending_views: Redis unavailable, nothing flushed", exc_info=True)
        return {"applied": 0, "skipped": 0}

    if not keys:
        return {"applied": 0, "skipped": 0}

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ViewCountStmts

    session = get_cassandra_session()
    applied = 0
    skipped = 0
    for key in keys:
        raw_id = key[len(VIEW_PENDING_PREFIX) :]
        aid = None
        with contextlib.suppress(ValueError, TypeError):
            aid = UUID(raw_id)
        if aid is None:
            # Malformed key somehow got in -- drop it so it doesn't jam every
            # future cycle re-scanning something it can never apply.
            with contextlib.suppress(Exception):
                client.delete(key)
            skipped += 1
            continue

        # GETDEL: atomic read+clear of this one key. A view landing between
        # the GETDEL and the Cassandra write below just creates a fresh key
        # for the next cycle to pick up -- the accepted race from this
        # feature's design (see the docstring above).
        try:
            delta = client.getdel(key)
        except Exception:
            logger.warning("flush_pending_views: failed to read/clear %s", key, exc_info=True)
            skipped += 1
            continue
        if not delta:
            continue
        try:
            session.execute(ViewCountStmts.BUMP, (int(delta), aid))
            applied += 1
        except Exception:
            logger.warning(
                "flush_pending_views: Cassandra bump failed for article %s", raw_id, exc_info=True
            )
            skipped += 1

    return {"applied": applied, "skipped": skipped}


def get_views_bulk(article_ids: list[str]) -> dict[str, int]:
    """Map article_id -> view count for the given ids (missing = 0)."""
    uuids: list[UUID] = []
    for aid in article_ids:
        try:
            uuids.append(UUID(aid))
        except (ValueError, TypeError):
            continue
    if not uuids:
        return {}
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ViewCountStmts

        # IN on the partition key; callers cap the list to a small recent window.
        # Prepared `IN ?` binds the id list directly (no client-side `[...]`
        # rendering of the old `IN %s` SimpleStatement).
        rows = get_cassandra_session().execute(ViewCountStmts.GET_BULK, (uuids,))
    except Exception:
        return {}
    return {str(row.article_id): int(row.views) for row in rows if row.views is not None}
