"""Owner-attached evidence for a specific article -- workers-side READ access to `article_admin_sources`.

See docs/newspaper-article-sources-design.md. Canonical write side (attach /
soft-remove) lives in backend's admin API
(backend/app/modules/admin/admin_source_store.py, behind
POST/DELETE /api/v1/admin/articles/:article_id/sources) -- this module only
needs to LOAD the active sources at recompose time and project them into a
compose session's prompt + trace (see
app/modules/ai/llm_compose.py's `compose_scrape_article(admin_sources=...)`
and app/modules/newspaper/tasks/publish_tasks.py's `recompose_review` /
`recompose_published` wiring).

Both services share the prepared statements (`AdminSourceStmts`, imported
from `algorand_shared.admin_source_statements` and re-exported by
`app.core.statements`) -- per CLAUDE.md section 3, the CQL is not duplicated
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Bounded: nobody attaches anywhere near this many sources to one article
# (design doc section 2.1's own "nobody attaches 50" note) -- this is a
# generous INTERNAL fetch cap, not the caller-facing page size, so that
# filtering out a handful of removed rows in Python still leaves room for a
# genuine ~50 active sources. See AdminSourceStmts' own docstring for why
# `status` is filtered here rather than in CQL.
_FETCH_LIMIT = 200
# Caller-facing bound: "the newest 50 active sources win" (design doc).
_ACTIVE_LIMIT = 50


@dataclass(frozen=True)
class AdminSource:
    """One owner-attached evidence item for an article (an active `article_admin_sources` row)."""

    source_id: str
    added_by: str
    label: str
    kind: str
    attribution_url: str
    content: str
    added_at: datetime | None


def load_active_sources(article_id: str, *, limit: int = _ACTIVE_LIMIT) -> list[AdminSource]:
    """Active (``status == 'active'``) owner-attached sources for an article, newest first, bounded to ``limit``.

    Raises on a genuine Cassandra read error -- deliberately NOT best-effort.
    Callers on an admin-triggered recompose path must fail CLOSED (design doc
    section 3's "Failure semantics at the seam", CLAUDE.md invariant #8: an
    error must never be presented downstream as "none found"): a swallowed
    exception here would silently proceed without the owner's evidence and
    burn a paid compose producing the same inadequate article the owner
    attached the source specifically to fix. "No rows" (an empty list) is the
    normal no-sources-attached case, not an error -- see `recompose_review`
    / `recompose_published` in tasks/publish_tasks.py for how each catches
    an exception from this function and NOT an empty list.
    """
    if not article_id:
        return []
    from uuid import UUID

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import AdminSourceStmts

    try:
        aid = UUID(str(article_id))
    except ValueError:
        return []
    session = get_cassandra_session()
    rows = session.execute(AdminSourceStmts.LIST_ACTIVE_BY_ARTICLE, (aid, _FETCH_LIMIT))
    active = [r for r in rows if (getattr(r, "status", "") or "") == "active"]
    return [
        AdminSource(
            source_id=str(r.source_id),
            added_by=r.added_by or "",
            label=r.label or "",
            kind=r.kind or "text",
            attribution_url=r.attribution_url or "",
            content=r.content or "",
            added_at=r.added_at,
        )
        for r in active[:limit]
    ]
