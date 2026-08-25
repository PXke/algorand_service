"""Deliberately-deleted article lookups (the 410 Gone set).

Article state, not an SEO concern — both the HTML document route and the JSON
article endpoint need it to answer "removed" (410) rather than "never existed"
(404), and admin writes the tombstone (`articles`.status='deleted') on
delete. Kept here so those callers share one definition instead of each
hand-rolling the ArticlesStmts.GET_BY_ID + status check.

Single-id point lookup only. The sitemap builder needs the whole set at once
and caches it briefly (see seo/sitemap.py); that stays separate on purpose —
different access pattern, different cache lifetime.
"""

from __future__ import annotations

import logging
from uuid import UUID

logger = logging.getLogger(__name__)


def is_article_tombstoned(article_id: str) -> bool:
    """Was this article deliberately deleted (vs never existing)?

    Fails OPEN (False): a Cassandra hiccup must degrade to a plain 404, never
    break the article route or wrongly claim a live article is gone.

    2026-08-24: reads `articles` directly (status='deleted' is dual-written
    on every delete). Briefly needed a fallback to the legacy
    `deleted_articles` table for 171 tombstones predating the article-table
    consolidation (their `articles_by_id` row was already hard-deleted by
    the time the migration ran, so there was nothing to carry forward) --
    those 171 rows were pruned from `deleted_articles` (owner decision: a
    handful of old dead URLs serving 404 instead of 410 was an acceptable
    trade for not permanently carrying a legacy-table fallback), so
    `articles` is now the sole, always-sufficient source.
    """
    try:
        from algorand_shared.article_statements import ArticlesStmts

        from app.core.cassandra import get_cassandra_session

        aid = UUID(article_id)
        row = get_cassandra_session().execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
        return row is not None and row.status == "deleted"
    except Exception:
        logger.debug("tombstone lookup failed for %s — treating as not deleted", article_id)
        return False
