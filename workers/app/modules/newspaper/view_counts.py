"""Worker-side reader (and, since 2026-08-25, flusher) for the per-article read tally.

The tally lives on `articles.views` (plain int column, migration 084 --
replacing the separate article_view_counts counter table, whose atomic
increments this write-behind design no longer needs). Increments don't happen
directly on backend's article-detail fetch -- that hot path only INCRs a
per-article pending key in Redis (app/modules/news/stores/view_counts.py's
record_view, keys prefixed "news:views:pending:"). flush_pending_views drains
those onto articles.views on a Celery beat schedule (see celery_app.py):
read the current tally, add the drained delta, write the new total back via
article_store.update_article_views -- which re-reads the row's CURRENT
partition key before patching, because published_at (part of `articles`'
partition key) moves on a recompose re-publish and a stale key would upsert
a phantom row nothing reads.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.redis_client import get_redis

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

# Must match backend's app/modules/news/stores/view_counts.VIEW_PENDING_PREFIX.
VIEW_PENDING_PREFIX = "news:views:pending:"
_VIEW_PENDING_MATCH = VIEW_PENDING_PREFIX + "*"


def _redis_client() -> redis.Redis:
    return get_redis(socket_connect_timeout=2)


def flush_pending_views() -> dict[str, int]:
    """Drain Redis's pending per-article view increments onto articles.views.

    Runs every 10 minutes as a Celery beat task -- see celery_app.py's
    "flush-pending-view-counts" entry. Trades real-time accuracy for a fixed
    reconciliation window: nothing reads the view tally for anything exact or
    sub-10-minute (hot_feed's velocity ranking floors article age at 6 hours,
    so a 10-minute lag is noise at that granularity). A worker crash
    mid-window loses at most one window's worth of increments for a
    vanity/ranking metric -- self-correcting on the next real view, not worth
    designing around further.

    Since migration 084 the write is read-current-total + add-delta +
    write-back on a plain int column, not a counter bump. Safe because this
    beat is the tally's ONLY writer (a single periodic task, so no concurrent
    read-modify-write on the same article) and the read-to-write gap is
    milliseconds, not the 10-minute window -- accepted by the owner with the
    schema merge. update_article_views itself re-reads the row's CURRENT
    partition key before patching (phantom-row safety across recompose
    re-publishes).

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

    from app.modules.newspaper.article_store import get_article_views, update_article_views

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
            current = get_article_views(raw_id)
            if current is None:
                # No such article any more (fully purged, not just
                # status='deleted' -- deleted rows still resolve). Drop the
                # delta: a tally for a nonexistent article has nowhere to
                # live, and re-parking it would jam every future cycle.
                skipped += 1
                continue
            if update_article_views(raw_id, current + int(delta)):
                applied += 1
            else:
                skipped += 1
        except Exception:
            logger.warning(
                "flush_pending_views: views update failed for article %s", raw_id, exc_info=True
            )
            skipped += 1

    return {"applied": applied, "skipped": skipped}


def get_views_bulk(article_ids: list[str]) -> dict[str, int]:
    """Map article_id -> view count for the given ids (missing = 0)."""
    pairs: list[tuple[str, UUID]] = []
    for aid in article_ids:
        try:
            pairs.append((aid, UUID(aid)))
        except (ValueError, TypeError):
            continue
    if not pairs:
        return {}
    try:
        from algorand_shared.article_statements import ArticlesStmts

        from app.core.cassandra import execute_parallel_with_args

        # One SAI point-lookup per id, in parallel -- articles.views replaced
        # the old counter table's `IN ?` partition-key read (article_id is an
        # SAI-indexed column on `articles`, not its partition key, so IN
        # doesn't apply). Callers cap the list to a small recent window.
        results = execute_parallel_with_args(
            ArticlesStmts.GET_VIEWS_BY_ID, [(aid,) for _, aid in pairs], raise_on_error=False
        )
    except Exception:
        return {}
    counts: dict[str, int] = {}
    for (raw, _), (ok, result) in zip(pairs, results, strict=True):
        if not ok:
            continue
        row = result.one() if hasattr(result, "one") else None
        if row is not None and row.views is not None:
            counts[raw] = int(row.views)
    return counts
