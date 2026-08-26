"""Prepared CQL for the editorial-room `artifacts` / `artifacts_pending` / `artifact_content` / `to_compose` tables.

Moved here from workers/app/core/statements.py (2026-08-26) alongside
`algorand_shared.artifact_store` and `algorand_shared.to_compose_selection` so
backend's admin artifact/to-compose routes can read (and, for the pin/reset
routes, write) these tables directly instead of round-tripping through a
Celery task into a worker process -- see the article-consolidation-style
precedent in `article_statements.py` (this is the same shape of move, just for
a newer table pair). Workers' own `app/core/statements.py` re-exports
`ArtifactStmts`/`ToComposeStmts` from here so existing workers call sites
(`from app.core.statements import ArtifactStmts`) keep working unchanged.

Unlike `article_statements.py`'s legacy-dedup flat constants, these are full
classes (like `ArticlesStmts`) since this is the actual, only interface both
services use going forward, not a temporary de-dup of two independently
hand-maintained copies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cassandra.query import PreparedStatement


class _Stmt:
    """Descriptor holding CQL; resolves to the (cached) PreparedStatement on access.

    Preparation is delegated to `app.core.cassandra.prepare_cached` -- resolved
    per-process, so this works identically whether accessed from backend or
    workers, each of which has its own `app.core.cassandra` module.
    """

    def __init__(self, cql: str) -> None:
        self.cql = cql

    def __get__(self, obj: object | None, owner: type | None) -> PreparedStatement:
        from app.core.cassandra import prepare_cached

        return prepare_cached(self.cql)


class ArtifactStmts:
    """Prepared statements for the artifacts / artifacts_pending / artifact_content tables."""

    INSERT = _Stmt(
        "INSERT INTO algorand_platform.artifacts ("
        "artifact_id, service_id, venue_service_id, url, channel, created_at, event_date, "
        "priority, priority_computed_at, status, human_pick_day"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_PENDING = _Stmt(
        "INSERT INTO algorand_platform.artifacts_pending ("
        "status, priority, created_at, artifact_id, service_id, venue_service_id, channel, url, "
        "event_date, human_pick_day"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    DELETE_PENDING = _Stmt(
        "DELETE FROM algorand_platform.artifacts_pending "
        "WHERE status = ? AND priority = ? AND created_at = ? AND artifact_id = ?"
    )
    LIST_PENDING = _Stmt(
        "SELECT status, priority, created_at, artifact_id, service_id, venue_service_id, channel, "
        "url, event_date, human_pick_day "
        "FROM algorand_platform.artifacts_pending WHERE status = ? LIMIT ?"
    )
    SET_PENDING_HUMAN_PICK = _Stmt(
        "UPDATE algorand_platform.artifacts_pending SET human_pick_day = ? "
        "WHERE status = ? AND priority = ? AND created_at = ? AND artifact_id = ?"
    )
    # Ongoing bug-class-2 reconciliation (service_reconciliation.py) backfills
    # venue_service_id on a pending artifact that plausibly should have one but
    # doesn't -- see set_artifact_venue_service_id in artifact_store.py. Not part
    # of either table's primary/clustering key, so both are plain UPDATEs.
    SET_VENUE_SERVICE_ID = _Stmt(
        "UPDATE algorand_platform.artifacts SET venue_service_id = ? WHERE artifact_id = ?"
    )
    SET_PENDING_VENUE_SERVICE_ID = _Stmt(
        "UPDATE algorand_platform.artifacts_pending SET venue_service_id = ? "
        "WHERE status = ? AND priority = ? AND created_at = ? AND artifact_id = ?"
    )
    GET = _Stmt(
        "SELECT artifact_id, service_id, venue_service_id, url, channel, created_at, event_date, "
        "priority, priority_computed_at, status, human_pick_day "
        "FROM algorand_platform.artifacts WHERE artifact_id = ?"
    )
    GET_STATUS_ROW = _Stmt(
        "SELECT status, priority, created_at FROM algorand_platform.artifacts WHERE artifact_id = ?"
    )
    UPDATE_STATUS = _Stmt(
        "UPDATE algorand_platform.artifacts SET status = ? WHERE artifact_id = ?"
    )
    UPDATE_PRIORITY = _Stmt(
        "UPDATE algorand_platform.artifacts SET priority = ?, priority_computed_at = ? "
        "WHERE artifact_id = ?"
    )
    SET_HUMAN_PICK = _Stmt(
        "UPDATE algorand_platform.artifacts SET human_pick_day = ? WHERE artifact_id = ?"
    )
    CLEAR_HUMAN_PICK = _Stmt(
        "UPDATE algorand_platform.artifacts SET human_pick_day = null WHERE artifact_id = ?"
    )
    INSERT_CONTENT = _Stmt(
        "INSERT INTO algorand_platform.artifact_content (artifact_id, title, content, metadata) "
        "VALUES (?, ?, ?, ?)"
    )
    GET_CONTENT = _Stmt(
        "SELECT artifact_id, title, content, metadata "
        "FROM algorand_platform.artifact_content WHERE artifact_id = ?"
    )


class ToComposeStmts:
    """Prepared statements for the to_compose day-ahead selection table."""

    INSERT = _Stmt(
        "INSERT INTO algorand_platform.to_compose ("
        "compose_day, slot, artifact_id, lane, service_id, picked_at"
        ") VALUES (?, ?, ?, ?, ?, ?)"
    )
    LIST_FOR_DAY = _Stmt(
        "SELECT compose_day, slot, artifact_id, lane, service_id, picked_at "
        "FROM algorand_platform.to_compose WHERE compose_day = ?"
    )
    DELETE_FOR_DAY = _Stmt("DELETE FROM algorand_platform.to_compose WHERE compose_day = ?")
    # Unfiltered full-table scan -- no WHERE clause, so no ALLOW FILTERING
    # needed even though compose_day is the partition key. Safe: this table
    # holds at most NEWS_MAX_ARTICLES_PER_DAY rows per day, forever, so even
    # a year of history is a few thousand rows. Backs
    # to_compose_selection.find_stale_selected_artifacts, which needs every
    # past day's rows and has no cheap way to enumerate which days exist
    # otherwise (added 2026-08-26).
    LIST_ALL = _Stmt(
        "SELECT compose_day, slot, artifact_id, lane, service_id, picked_at "
        "FROM algorand_platform.to_compose"
    )
