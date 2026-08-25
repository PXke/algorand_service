"""Status/published_at transitions on the new `articles` table.

Shared by backend (admin actions: delete, draft-toggle, review-approve) and
workers (compose-time actions: create, recompose, backlog-release), since both
services perform the exact same delete-old-partition+insert-new-partition
dance on the exact same table. A previous version of this lived only in
workers' article_store.py; duplicating it into backend's admin store would
have been the exact "two independent implementations of the same table's
write path" bug this whole consolidation exists to kill.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

_ARTICLES_COLUMNS = (
    "status", "year", "published_at", "article_id", "service_id", "title", "summary", "body",
    "image_url", "tags", "source_url", "trigger_txid", "trigger_round", "slug", "translations",
    "first_published_at", "updated_at", "prompt_version", "composed_by_model",
    "deleted_at", "status_updated_at",
)  # fmt: skip


def transition_article_status(
    article_id: UUID,
    *,
    new_status: str | None = None,
    new_published_at: datetime | None = None,
    **column_overrides: object,
) -> bool:
    """Move an `articles` row to a new status and/or published_at, preserving every other column. new_status=None keeps the row's current status (e.g. a recompose that only moves published_at + content). Returns False if no existing row was found.

    status/year/published_at are the partition/clustering key on `articles`,
    so a status transition can never be a plain UPDATE -- it's DELETE the old
    partition row + INSERT a new one. Every column not explicitly overridden
    must be carried forward verbatim, or the transition silently drops it
    (the same "partial feed upsert" bug class the OLD schema's
    replace_article_content/update_article comments already warn about).
    """
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.article_statements import ArticlesStmts

    session = get_cassandra_session()
    old = session.execute(ArticlesStmts.GET_FULL_BY_ID, (article_id,)).one()
    if old is None:
        return False
    session.execute(ArticlesStmts.DELETE, (old.status, old.year, old.published_at, article_id))

    published_at = new_published_at or old.published_at
    values: dict[str, object] = {col: getattr(old, col) for col in _ARTICLES_COLUMNS}
    values["status"] = new_status or old.status
    values["published_at"] = published_at
    values["year"] = published_at.year
    # A genuine status change (not just published_at moving, e.g. a
    # recompose re-publish that keeps status='published') stamps
    # status_updated_at -- "when did this row's status last change," the
    # generic counterpart to deleted_at (which only covers the 'deleted'
    # transition specifically).
    if new_status is not None and new_status != old.status:
        values["status_updated_at"] = datetime.now(tz=UTC)
    values.update(column_overrides)
    session.execute(ArticlesStmts.INSERT, tuple(values[col] for col in _ARTICLES_COLUMNS))
    return True
