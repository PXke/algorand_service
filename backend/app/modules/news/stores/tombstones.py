"""Deliberately-deleted article lookups (the 410 Gone set).

Article state, not an SEO concern — both the HTML document route and the JSON
article endpoint need it to answer "removed" (410) rather than "never existed"
(404), and admin writes the tombstone on delete. Kept here so those callers
share one definition instead of each hand-rolling DeletedArticleStmts.

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

    2026-08-24: checks `articles` first (status='deleted' is dual-written on
    every delete since the article-table consolidation), falling back to the
    legacy `deleted_articles` table for tombstones predating it. Live count
    comparison found 171 of 309 `deleted_articles` rows have NO matching
    `articles` row at all -- their `articles_by_id` row was already
    hard-deleted by the time the original data migration ran, so there was
    nothing to carry forward. `deleted_articles` stays authoritative for
    that historical set until/unless it's explicitly backfilled -- treating
    `articles` as the sole source here would have silently turned 171 real
    410s into 404s.
    """
    try:
        from algorand_shared.article_statements import ArticlesStmts

        from app.core.cassandra import get_cassandra_session

        aid = UUID(article_id)
        session = get_cassandra_session()
        row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
        if row is not None:
            return row.status == "deleted"

        from app.core.statements import DeletedArticleStmts

        legacy_row = session.execute(DeletedArticleStmts.GET, (aid,)).one()
        return legacy_row is not None
    except Exception:
        logger.debug("tombstone lookup failed for %s — treating as not deleted", article_id)
        return False
