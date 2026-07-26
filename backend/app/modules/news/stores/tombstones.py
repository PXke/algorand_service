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
    """
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import DeletedArticleStmts

        row = get_cassandra_session().execute(DeletedArticleStmts.GET, (UUID(article_id),)).one()
        return row is not None
    except Exception:
        logger.debug("tombstone lookup failed for %s — treating as not deleted", article_id)
        return False
