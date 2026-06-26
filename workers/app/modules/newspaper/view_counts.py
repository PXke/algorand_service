"""Worker-side reader for the per-article read tally written by the API.

Shares the article_view_counts counter table (migration 026). Read-only here;
increments happen in the backend on article-detail fetch.
"""

from __future__ import annotations

from uuid import UUID


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

        # IN on the partition key; callers cap the list to a small recent window.
        rows = get_cassandra_session().execute(
            "SELECT article_id, views FROM article_view_counts WHERE article_id IN %s",
            (tuple(uuids),),
        )
    except Exception:
        return {}
    return {
        str(row.article_id): int(row.views)
        for row in rows
        if row.views is not None
    }
