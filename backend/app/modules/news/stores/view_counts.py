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
