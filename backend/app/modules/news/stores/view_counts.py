"""Per-article read tally (Cassandra counter).

Decoupled from the ArticleStore Protocol: a no-op when the API runs the
in-memory store (dev/tests), so endpoints can always call record_view safely.
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.core.config import settings

logger = logging.getLogger(__name__)


def _cassandra_enabled() -> bool:
    return settings.news_store.strip().lower() == "cassandra"


def _as_uuid(article_id: str) -> UUID | None:
    try:
        return UUID(article_id)
    except (ValueError, TypeError):
        return None


def record_view(article_id: str) -> None:
    """Best-effort +1; never raises into the request path."""
    if not _cassandra_enabled():
        return
    aid = _as_uuid(article_id)
    if aid is None:
        return
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ViewCountStmts

        get_cassandra_session().execute(ViewCountStmts.BUMP, (aid,))
    except Exception:
        logger.warning("failed to bump view count for article %s", article_id, exc_info=True)


def get_views(article_id: str) -> int:
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
