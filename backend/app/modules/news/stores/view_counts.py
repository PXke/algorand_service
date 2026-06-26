"""Per-article read tally (Cassandra counter).

Decoupled from the ArticleStore Protocol: a no-op when the API runs the
in-memory store (dev/tests), so endpoints can always call record_view safely.
"""

from __future__ import annotations

from uuid import UUID

from app.core.config import settings


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

        get_cassandra_session().execute(
            "UPDATE article_view_counts SET views = views + 1 WHERE article_id = %s",
            (aid,),
        )
    except Exception:
        pass


def get_views(article_id: str) -> int:
    if not _cassandra_enabled():
        return 0
    aid = _as_uuid(article_id)
    if aid is None:
        return 0
    try:
        from app.core.cassandra import get_cassandra_session

        row = get_cassandra_session().execute(
            "SELECT views FROM article_view_counts WHERE article_id = %s",
            (aid,),
        ).one()
    except Exception:
        return 0
    return int(row.views) if row and row.views is not None else 0
