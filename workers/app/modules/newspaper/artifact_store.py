"""Cassandra-backed store for the editorial-room `artifacts` table (2026-08-25, SHADOW MODE).

This is a fully additive replacement candidate for `publish_queue` (see
`app.modules.newspaper.publish_queue_store`), NOT yet read by the live
compose/publish path. It mirrors that module's own shape closely on purpose:

  - `artifacts` (thin, frequently scanned) + `artifacts_pending` (a
    status-partitioned pending index) mirrors `publish_queue` +
    `publish_queue_pending`.
  - `artifact_content` (raw diff/tweet/transcript/mail body, one row per
    artifact) mirrors the `articles` / `article_history` content split.
  - The "at most one PENDING artifact per service_id" dedup mirrors
    `publish_queue_store.enqueue_publish`'s own scan-and-replace mechanism
    exactly: a new artifact for a service_id that already has a pending
    artifact deletes the old one (both from `artifacts` and its pending
    index row) and inserts the new one, rather than accumulating.

See `app.modules.newspaper.artifact_priority` for the priority sweep that
updates `priority`/`priority_computed_at` on these rows, and
`app.modules.newspaper.to_compose_selection` for the day-ahead selection
logic that reads them.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Statuses an artifact can carry. Only "pending" artifacts are visible in the
# artifacts_pending index / eligible for the priority sweep or selection.
PENDING = "pending"
SELECTED = "selected"
COMPOSED = "composed"
DISCARDED = "discarded"


@dataclass(frozen=True)
class Artifact:
    """One row in the `artifacts` table."""

    artifact_id: str
    service_id: str | None
    url: str | None
    channel: str
    created_at: datetime
    event_date: datetime | None
    priority: float
    priority_computed_at: datetime | None
    status: str
    human_pick_day: str | None = None

    @property
    def effective_event_date(self) -> datetime:
        """`event_date` when extractable, else the fallback `created_at` (per the design's explicit fallback rule)."""
        return self.event_date or self.created_at


@dataclass(frozen=True)
class ArtifactContent:
    """One row in the `artifact_content` table."""

    artifact_id: str
    title: str
    content: str
    metadata: dict[str, Any]


def insert_artifact(
    *,
    service_id: str | None,
    url: str | None,
    channel: str,
    content: str,
    title: str = "",
    metadata: dict[str, Any] | None = None,
    event_date: datetime | None = None,
    now: datetime | None = None,
) -> tuple[str, bool]:
    """Insert a new pending artifact plus its content row. Returns (artifact_id, created).

    Dedup invariant: at most one PENDING artifact per service_id. When
    `service_id` is truthy and an existing pending artifact for it is found,
    that old artifact (and its content row) is replaced -- deleted, then the
    new one inserted -- rather than accumulated, mirroring
    publish_queue_store.enqueue_publish's identical rule. `service_id` is
    frequently None (a brief, a mail message with no linked service) --
    those never dedup against each other or anything else.
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArtifactStmts

    session = get_cassandra_session()
    now = now or datetime.now(tz=UTC)

    if service_id:
        for row in session.execute(ArtifactStmts.LIST_PENDING, (PENDING, 2000)):
            if row.service_id == service_id:
                _delete_pending_row(
                    session,
                    status=row.status,
                    priority=row.priority,
                    created_at=row.created_at,
                    artifact_id=row.artifact_id,
                )
                session.execute(ArtifactStmts.UPDATE_STATUS, (DISCARDED, row.artifact_id))
                break

    artifact_id = uuid.uuid4()
    priority = 0.0
    session.execute(
        ArtifactStmts.INSERT,
        (
            artifact_id,
            service_id,
            url,
            channel,
            now,
            event_date,
            priority,
            None,
            PENDING,
            None,
        ),
    )
    session.execute(
        ArtifactStmts.INSERT_PENDING,
        (PENDING, priority, now, artifact_id, service_id, channel, url, event_date, None),
    )
    session.execute(
        ArtifactStmts.INSERT_CONTENT,
        (artifact_id, title, content, json.dumps(metadata or {}, separators=(",", ":"))),
    )
    return str(artifact_id), True


def _delete_pending_row(
    session: object, *, status: str, priority: float, created_at: datetime, artifact_id: uuid.UUID
) -> None:
    from app.core.statements import ArtifactStmts

    session.execute(
        ArtifactStmts.DELETE_PENDING,
        (status, priority, created_at, artifact_id),
    )


def _row_to_artifact(row: object) -> Artifact:
    return Artifact(
        artifact_id=str(row.artifact_id),
        service_id=row.service_id or None,
        url=row.url or None,
        channel=row.channel or "",
        created_at=row.created_at,
        event_date=row.event_date,
        priority=float(row.priority or 0.0),
        priority_computed_at=row.priority_computed_at,
        status=row.status or "",
        human_pick_day=row.human_pick_day or None,
    )


def list_pending_artifacts(*, limit: int = 2000) -> list[Artifact]:
    """Load pending artifacts from the pending index, priority DESC, created_at ASC (the index's own clustering order)."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArtifactStmts

    session = get_cassandra_session()
    return [
        Artifact(
            artifact_id=str(row.artifact_id),
            service_id=row.service_id or None,
            url=row.url or None,
            channel=row.channel or "",
            created_at=row.created_at,
            event_date=row.event_date,
            priority=float(row.priority or 0.0),
            priority_computed_at=None,
            status=row.status or PENDING,
            human_pick_day=row.human_pick_day or None,
        )
        for row in session.execute(ArtifactStmts.LIST_PENDING, (PENDING, limit))
    ]


def get_artifact(artifact_id: str) -> Artifact | None:
    """Load one artifact by id regardless of status."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArtifactStmts

    try:
        aid = uuid.UUID(str(artifact_id))
    except ValueError:
        return None
    session = get_cassandra_session()
    row = session.execute(ArtifactStmts.GET, (aid,)).one()
    if row is None:
        return None
    return _row_to_artifact(row)


def get_artifact_content(artifact_id: str) -> ArtifactContent | None:
    """Load one artifact's raw content by id."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArtifactStmts

    try:
        aid = uuid.UUID(str(artifact_id))
    except ValueError:
        return None
    session = get_cassandra_session()
    row = session.execute(ArtifactStmts.GET_CONTENT, (aid,)).one()
    if row is None:
        return None
    try:
        metadata = json.loads(row.metadata or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return ArtifactContent(
        artifact_id=str(row.artifact_id),
        title=row.title or "",
        content=row.content or "",
        metadata=metadata,
    )


def update_artifact_priority(artifact_id: str, priority: float, *, now: datetime | None = None) -> None:
    """Persist a freshly-swept priority on an artifact and re-key its pending-index row (priority is part of that index's clustering key, so this is delete-old + insert-new, not an UPDATE -- only reindexes when the artifact is still pending)."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArtifactStmts

    try:
        aid = uuid.UUID(str(artifact_id))
    except ValueError:
        return
    session = get_cassandra_session()
    now = now or datetime.now(tz=UTC)

    status_row = session.execute(ArtifactStmts.GET_STATUS_ROW, (aid,)).one()
    if status_row is None:
        return

    session.execute(ArtifactStmts.UPDATE_PRIORITY, (priority, now, aid))

    if status_row.status == PENDING and status_row.created_at is not None:
        full = session.execute(ArtifactStmts.GET, (aid,)).one()
        _delete_pending_row(
            session,
            status=PENDING,
            priority=status_row.priority,
            created_at=status_row.created_at,
            artifact_id=aid,
        )
        session.execute(
            ArtifactStmts.INSERT_PENDING,
            (
                PENDING,
                priority,
                status_row.created_at,
                aid,
                full.service_id if full else None,
                full.channel if full else None,
                full.url if full else None,
                full.event_date if full else None,
                full.human_pick_day if full else None,
            ),
        )


def mark_artifact_status(artifact_id: str, status: str) -> None:
    """Move an artifact out of (or within) the pending lane: selected / composed / discarded. Removes its pending-index row whenever the new status isn't "pending"."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArtifactStmts

    try:
        aid = uuid.UUID(str(artifact_id))
    except ValueError:
        return
    session = get_cassandra_session()

    status_row = session.execute(ArtifactStmts.GET_STATUS_ROW, (aid,)).one()
    if status_row is None:
        return

    session.execute(ArtifactStmts.UPDATE_STATUS, (status, aid))

    if status_row.status == PENDING and status != PENDING and status_row.created_at is not None:
        _delete_pending_row(
            session,
            status=PENDING,
            priority=status_row.priority,
            created_at=status_row.created_at,
            artifact_id=aid,
        )


def _set_pending_index_pin(
    session: object, aid: uuid.UUID, status_row: object, day: str | None
) -> None:
    """Mirror a human_pick_day change onto the pending-index row, when the artifact is currently pending. human_pick_day isn't part of that table's primary key, so this is a plain UPDATE (unlike the priority delete+reinsert dance)."""
    from app.core.statements import ArtifactStmts

    if status_row.status == PENDING and status_row.created_at is not None:
        session.execute(
            ArtifactStmts.SET_PENDING_HUMAN_PICK,
            (day, PENDING, status_row.priority, status_row.created_at, aid),
        )


def pin_artifact_for_day(artifact_id: str, day: str) -> bool:
    """Admin-facing hook: pin an artifact as tomorrow's (or any future day's) human pick. Returns False for an unknown id."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArtifactStmts

    try:
        aid = uuid.UUID(str(artifact_id))
    except ValueError:
        return False
    session = get_cassandra_session()
    status_row = session.execute(ArtifactStmts.GET_STATUS_ROW, (aid,)).one()
    if status_row is None:
        return False
    session.execute(ArtifactStmts.SET_HUMAN_PICK, (day, aid))
    _set_pending_index_pin(session, aid, status_row, day)
    return True


def clear_artifact_pin(artifact_id: str) -> None:
    """Clear a spent (or superseded) human pin, mirroring publish_queue_store.clear_human_pick."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArtifactStmts

    try:
        aid = uuid.UUID(str(artifact_id))
    except ValueError:
        return
    session = get_cassandra_session()
    status_row = session.execute(ArtifactStmts.GET_STATUS_ROW, (aid,)).one()
    session.execute(ArtifactStmts.CLEAR_HUMAN_PICK, (aid,))
    if status_row is not None:
        _set_pending_index_pin(session, aid, status_row, None)
