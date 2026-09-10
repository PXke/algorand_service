"""Cassandra store for `article_admin_sources` -- backend-side WRITE access (attach / list-with-preview / soft-remove).

See docs/newspaper-article-sources-design.md. Canonical read side (loading
active sources at recompose time) lives in workers'
`app/modules/newspaper/admin_source_store.py`; this module is the write side
behind `backend/app/modules/admin/api/routes.py`'s
POST/GET/DELETE `/api/v1/admin/articles/:article_id/sources` handlers.

Both services share the prepared statements (`AdminSourceStmts`, imported
from `algorand_shared.admin_source_statements` and re-exported by
`app.core.statements`) -- per CLAUDE.md section 3, the CQL is not duplicated
here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.schemas import AdminSourceItem

# Admin-facing list preview length -- the design doc doesn't pin an exact
# value ("doesn't pin an exact preview length; pick something reasonable
# like 200 chars + content_length"). 200 chars is enough to recognize WHICH
# source this is (matches the label's own max_length) without shipping a
# full interview transcript on a page that only needs to show what's there.
PREVIEW_CHARS = 200

# Same internal-fetch-vs-caller-facing split as workers' own
# admin_source_store.py: a generous bound before filtering to active rows in
# Python (LIST_ACTIVE_BY_ARTICLE doesn't filter status in CQL -- see that
# statement's own docstring), then capped to the caller-facing page size.
_FETCH_LIMIT = 200
_ACTIVE_LIMIT = 50


def _epoch(dt: datetime | None) -> int:
    """UTC epoch seconds from a stored timestamp.

    The Cassandra driver returns timezone-NAIVE datetimes that are already UTC wall-clock values;
    calling .timestamp() directly makes Python assume the server's LOCAL zone and silently shift
    the result. Same normalisation as news/stores/cassandra.py.
    """
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def create_source(
    article_id: str, *, added_by: str, label: str, content: str, attribution_url: str
) -> str:
    """Attach a new owner-supplied source to an article. Returns the new source_id. Caller (the route handler) must validate the article exists and bound every field FIRST -- this function trusts its inputs."""
    from cassandra.util import uuid_from_time

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import AdminSourceStmts

    session = get_cassandra_session()
    article_uuid = UUID(article_id)
    now = datetime.now(tz=UTC)
    # timeuuid derived from the same `now` as added_at (matches
    # investigation_store.store_investigation_findings' own convention).
    source_id = uuid_from_time(now)
    session.execute(
        AdminSourceStmts.INSERT,
        (
            article_uuid,
            source_id,
            added_by,
            label,
            "text",
            attribution_url,
            content,
            "active",
            now,
            None,
        ),
    )
    return str(source_id)


def list_sources(article_id: str, *, limit: int = _ACTIVE_LIMIT) -> list[AdminSourceItem]:
    """Active sources attached to an article, newest first, bounded -- content elided to a preview (see PREVIEW_CHARS)."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import AdminSourceStmts

    session = get_cassandra_session()
    rows = session.execute(
        AdminSourceStmts.LIST_ACTIVE_BY_ARTICLE, (UUID(article_id), _FETCH_LIMIT)
    )
    active = [r for r in rows if (getattr(r, "status", "") or "") == "active"]
    items: list[AdminSourceItem] = []
    for r in active[:limit]:
        content = r.content or ""
        items.append(
            AdminSourceItem(
                source_id=str(r.source_id),
                article_id=str(r.article_id),
                added_by=r.added_by or "",
                label=r.label or "",
                kind=r.kind or "text",
                attribution_url=r.attribution_url or "",
                content_preview=content[:PREVIEW_CHARS],
                content_length=len(content),
                added_at_epoch=_epoch(r.added_at),
            )
        )
    return items


def soft_delete_source(article_id: str, source_id: str) -> bool:
    """Soft-remove a source (status='removed', never a real DELETE -- see migration 107's own comment on why). Returns True whether or not a matching row existed: `IF EXISTS` makes this idempotent-safe on a retry or an already-removed source, and Cassandra's LWT result carries no distinct "which case" signal worth surfacing -- the route 404s upstream if `article_id`/`source_id` are malformed, and a no-op removal is not itself an error."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import AdminSourceStmts

    session = get_cassandra_session()
    now = datetime.now(tz=UTC)
    session.execute(AdminSourceStmts.SOFT_DELETE, (now, UUID(article_id), UUID(source_id)))
    return True
