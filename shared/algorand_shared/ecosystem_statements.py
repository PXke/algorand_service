"""Prepared CQL for the Algorand Open Registry (roadmap item 26, `modules/ecosystem/`).

Shared between backend (submit/review/read routes, all in
`app/modules/ecosystem/`) and workers (the periodic liveness re-check beat
and the admin-only grounded blurb-draft helper, both in
`app/modules/ecosystem_probe/` -- see that module's own docstring). Lives
here rather than being duplicated in each service's own `app/core/
statements.py` per CLAUDE.md section 3 ("shared statements -> shared/
algorand_shared/*_statements.py, not both statements.py").

Five tables (migration 121):

- `ecosystem_projects` (slug PK) -- the canonical row. `status` is one of
  pending/approved/rejected (see `app.modules.ecosystem.models.domain`,
  backend-only, since only backend writes decisions). `last_probed_at` /
  `reachable` / `last_http_status` are the liveness fields: written once at
  submit time (backend, `SET_LIVENESS`) and refreshed by workers' periodic
  re-check beat (same statement) -- deliberately only on the canonical row,
  never denormalized into the by-category/by-tag projections below, so a
  liveness refresh never needs a projection rewrite. `draft_description` is
  the admin-only, never-auto-published DeepSeek blurb suggestion (workers
  writes it via `SET_DRAFT_DESCRIPTION`; only a human copying it into
  `description` through the ordinary edit/approve path ever changes what is
  actually served).
- `ecosystem_projects_by_domain` (domain PK) -- the LWT one-entry-per-domain
  dedupe gate (`INSERT_BY_DOMAIN_IF_ABSENT`), mirroring
  `x402_statements`/backend's own `X402DirectoryStmts.INSERT_LISTING_IF_ABSENT`
  pattern but keyed on domain (the real submission-uniqueness key here, see
  the design doc's section 3.2) rather than a url hash.
- `ecosystem_projects_by_category` ((category), approved_at DESC, slug) --
  written only on approve, deleted on reject/delete/edit-then-rewrite. Every
  approved entry also gets a second row under the reserved `"all"` pseudo-
  category (mirrors `x402_directory`'s `DIRECTORY_PARTITION` convention) so
  the unfiltered index is the same single-partition bounded read as a real
  category.
- `ecosystem_projects_by_tag` ((tag), approved_at DESC, slug) -- same shape,
  one row per free tag (up to `settings.ecosystem_max_tags`).
- `ecosystem_submissions_by_status` ((status), submitted_at DESC, slug) --
  the admin queue read; a row moves partition on every decision (delete at
  the old status, insert at the new one).

Same `_Stmt` descriptor shape as `x402_statements.py`: preparation is
delegated to whichever service's `app.core.cassandra.prepare_cached` is
importing this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cassandra.query import PreparedStatement

# Reserved by-category partition holding every approved entry regardless of
# its real category -- the unfiltered `GET /api/v1/ecosystem` index read.
# Never a real category value (validated against the closed enum, which does
# not contain "all"), so a submission can never forge its way into owning
# this partition's row for a slug other than its own.
ALL_CATEGORY_PARTITION = "all"

_CANONICAL_COLUMNS = (
    "slug, name, domain, url, repo_url, x402_url, description, category, tags, "
    "stage, open_source, source, status, editor_pick, contact, service_id, "
    "draft_description, submitted_at, reviewed_at, reviewed_by, reject_reason, "
    "last_probed_at, reachable, last_http_status, category_suggestion"
)

_PROJECTION_COLUMNS = (
    "slug, name, domain, url, description, tags, category, stage, open_source, "
    "editor_pick, x402_url"
)


class _Stmt:
    """Descriptor holding CQL; resolves to the (cached) PreparedStatement on access."""

    def __init__(self, cql: str) -> None:
        self.cql = cql

    def __get__(self, obj: object | None, owner: type | None) -> PreparedStatement:
        from app.core.cassandra import prepare_cached

        return prepare_cached(self.cql)


class EcosystemStmts:
    """Prepared statements for the Algorand Open Registry."""

    # -- canonical row -----------------------------------------------------
    GET = _Stmt(
        f"SELECT {_CANONICAL_COLUMNS} FROM algorand_platform.ecosystem_projects WHERE slug = ?"
    )
    UPSERT = _Stmt(
        "INSERT INTO algorand_platform.ecosystem_projects "
        f"({_CANONICAL_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    DELETE = _Stmt("DELETE FROM algorand_platform.ecosystem_projects WHERE slug = ?")
    SET_LIVENESS = _Stmt(
        "UPDATE algorand_platform.ecosystem_projects "
        "SET last_probed_at = ?, reachable = ?, last_http_status = ? WHERE slug = ?"
    )
    SET_DRAFT_DESCRIPTION = _Stmt(
        "UPDATE algorand_platform.ecosystem_projects SET draft_description = ? WHERE slug = ?"
    )
    # Bounded full-table scan for the periodic liveness re-check beat and
    # the seed action's "already exists" check -- the table holds at most a
    # few hundred rows in v1 (design doc section 8: "a Typesense index over
    # entries" is explicitly out of scope below ~500), so this is the same
    # "small, fully-enumerable table, one scan" shape glossary's
    # `GlossaryStmts.LIST_ALL` already uses, not an unbounded production read.
    LIST_ALL = _Stmt(f"SELECT {_CANONICAL_COLUMNS} FROM algorand_platform.ecosystem_projects")

    # -- by-domain (LWT dedupe gate) ----------------------------------------
    GET_BY_DOMAIN = _Stmt(
        "SELECT domain, slug, status FROM algorand_platform.ecosystem_projects_by_domain WHERE domain = ?"
    )
    INSERT_BY_DOMAIN_IF_ABSENT = _Stmt(
        "INSERT INTO algorand_platform.ecosystem_projects_by_domain (domain, slug, status) "
        "VALUES (?, ?, ?) IF NOT EXISTS"
    )
    UPDATE_BY_DOMAIN_STATUS = _Stmt(
        "UPDATE algorand_platform.ecosystem_projects_by_domain SET status = ? WHERE domain = ?"
    )
    DELETE_BY_DOMAIN = _Stmt(
        "DELETE FROM algorand_platform.ecosystem_projects_by_domain WHERE domain = ?"
    )

    # -- by-category (approved only) ----------------------------------------
    INSERT_BY_CATEGORY = _Stmt(
        "INSERT INTO algorand_platform.ecosystem_projects_by_category "
        f"(category, approved_at, {_PROJECTION_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    DELETE_BY_CATEGORY = _Stmt(
        "DELETE FROM algorand_platform.ecosystem_projects_by_category "
        "WHERE category = ? AND approved_at = ? AND slug = ?"
    )
    LIST_BY_CATEGORY = _Stmt(
        f"SELECT approved_at, {_PROJECTION_COLUMNS} FROM algorand_platform.ecosystem_projects_by_category "
        "WHERE category = ? LIMIT ?"
    )

    # -- by-tag (approved only) ---------------------------------------------
    INSERT_BY_TAG = _Stmt(
        "INSERT INTO algorand_platform.ecosystem_projects_by_tag "
        f"(tag, approved_at, {_PROJECTION_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    DELETE_BY_TAG = _Stmt(
        "DELETE FROM algorand_platform.ecosystem_projects_by_tag "
        "WHERE tag = ? AND approved_at = ? AND slug = ?"
    )
    LIST_BY_TAG = _Stmt(
        f"SELECT approved_at, {_PROJECTION_COLUMNS} FROM algorand_platform.ecosystem_projects_by_tag "
        "WHERE tag = ? LIMIT ?"
    )

    # -- by-status (admin queue) --------------------------------------------
    INSERT_BY_STATUS = _Stmt(
        "INSERT INTO algorand_platform.ecosystem_submissions_by_status "
        "(status, submitted_at, slug, name, domain, url, category, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    DELETE_BY_STATUS = _Stmt(
        "DELETE FROM algorand_platform.ecosystem_submissions_by_status "
        "WHERE status = ? AND submitted_at = ? AND slug = ?"
    )
    LIST_BY_STATUS = _Stmt(
        "SELECT submitted_at, slug, name, domain, url, category, source "
        "FROM algorand_platform.ecosystem_submissions_by_status WHERE status = ? LIMIT ?"
    )


__all__ = ["ALL_CATEGORY_PARTITION", "EcosystemStmts"]
