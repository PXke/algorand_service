"""Prepared CQL for `article_admin_sources` -- owner-attached evidence for a specific article (see docs/newspaper-article-sources-design.md).

Read side (LIST_ACTIVE_BY_ARTICLE) is used by workers/
(app/modules/newspaper/admin_source_store.load_active_sources, projected
into a recompose's prompt + trace -- see llm_compose.compose_scrape_article's
`admin_sources` parameter); write side (INSERT, SOFT_DELETE) is used by
backend/'s admin API (app/modules/admin/admin_source_store.py, wired into
app/modules/admin/api/routes.py's POST/DELETE .../sources handlers). Neither
service owns this table exclusively, so -- per CLAUDE.md section 3's shared-
CQL rule -- these statements live here, not duplicated into either service's
own statements.py, the same shape as ArtifactStmts/ToComposeStmts above
(a genuinely shared, ongoing interface, not a two-copy dedup like
article_statements.py's flat constants).

LIST_ACTIVE_BY_ARTICLE does NOT filter `status = 'active'` in CQL: `status`
is not part of this table's primary key (`PRIMARY KEY ((article_id),
source_id)`, migration 107), and CLAUDE.md section 4 forbids ALLOW FILTERING
on a non-key column. Callers fetch this LIMIT (a generous internal bound,
not the caller-facing page size) newest-first and filter+cap to active rows
in Python -- see admin_source_store.load_active_sources (workers) and the
backend list handler. This is a deliberate deviation from the design doc's
CQL sketch (which shows `status='active'` inline in the WHERE clause) to
honor CLAUDE.md's explicit ALLOW FILTERING ban; functionally equivalent at
this table's expected scale (a handful of sources per article -- the design
doc's own "nobody attaches 50" note).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cassandra.query import PreparedStatement


class _Stmt:
    """Descriptor holding CQL; resolves to the (cached) PreparedStatement on access.

    Preparation is delegated to `app.core.cassandra.prepare_cached` -- resolved
    per-process, so this works identically whether accessed from backend or
    workers, each of which has its own `app.core.cassandra` module. Same
    implementation as artifact_statements._Stmt / article_statements' module
    docstring explains why this is a plain descriptor and not, say, a
    functools.cached_property -- duplicated per shared module (not imported
    from one place) so each stays import-independent.
    """

    def __init__(self, cql: str) -> None:
        self.cql = cql

    def __get__(self, obj: object | None, owner: type | None) -> PreparedStatement:
        from app.core.cassandra import prepare_cached

        return prepare_cached(self.cql)


class AdminSourceStmts:
    """Prepared statements for the article_admin_sources table (migration 107)."""

    INSERT = _Stmt(
        "INSERT INTO algorand_platform.article_admin_sources ("
        "article_id, source_id, added_by, label, kind, attribution_url, content, "
        "status, added_at, removed_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    # See module docstring: no `status = 'active'` filter here (would require
    # ALLOW FILTERING) -- callers filter status in Python after this bounded,
    # newest-first, single-partition read.
    LIST_ACTIVE_BY_ARTICLE = _Stmt(
        "SELECT article_id, source_id, added_by, label, kind, attribution_url, content, "
        "status, added_at, removed_at "
        "FROM algorand_platform.article_admin_sources WHERE article_id = ? LIMIT ?"
    )
    # IF EXISTS: a soft-delete of a source_id that was never inserted (bad id,
    # already-removed race) is a no-op, not a phantom partial row -- same
    # precedent as the x402 probe badge clear (097) and promo deactivate (100).
    SOFT_DELETE = _Stmt(
        "UPDATE algorand_platform.article_admin_sources SET status = 'removed', removed_at = ? "
        "WHERE article_id = ? AND source_id = ? IF EXISTS"
    )
