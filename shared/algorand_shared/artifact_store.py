"""Cassandra-backed store for the editorial-room `artifacts` table.

Moved here from `workers/app/modules/newspaper/artifact_store.py` (2026-08-26)
so backend's admin artifact/to-compose routes (list-to-compose-selected,
get-artifact-content, to-compose-preview, pin-for-tomorrow, reset-to-compose)
can read (and, for pin/reset, write) these tables directly instead of a
synchronous Celery round-trip into a worker process -- see the shared-module
convention already established by `article_transitions.py` for the same
"both services perform the exact same table operations" shape. Workers still
does everything it always did with this module (ingest lanes calling
`insert_artifact`, the daily priority sweep, `service_reconciliation.py`'s
dedup/backfill passes) -- it just imports these functions from here now.

This replaced `publish_queue` as the compose/publish selection mechanism
(cut over 2026-08-25 -- see
`app.modules.newspaper.tasks.queue_drain_tasks.drain_to_compose`).
`publish_queue`/`publish_queue_pending`/`publish_queue_dedupe` were dropped
a day later, once this path proved stable in prod (their one-deploy-cycle
rollback-safety dual-write from ingest_signal.py/editorial_assignment.py was
removed in the same change). This module mirrored that (now-gone) module's
own shape closely on purpose, and still does structurally:

  - `artifacts` (thin, frequently scanned) + `artifacts_pending` (a
    status-partitioned pending index) mirrored `publish_queue` +
    `publish_queue_pending`.
  - `artifact_content` (raw diff/tweet/transcript/mail body, one row per
    artifact) mirrors the `articles` / `article_history` content split.
  - The "at most one PENDING artifact per service_id" dedup mirrored
    `publish_queue_store.enqueue_publish`'s own scan-and-replace mechanism
    for the ROW: a new artifact for a service_id that already has a pending
    artifact deletes the old row (both from `artifacts` and its pending
    index row) and inserts a new one. 2026-08-26: unlike publish_queue's
    version, the CONTENT is no longer replaced outright -- the new artifact's
    content is the old content plus the new content, concatenated (see
    insert_artifact's own docstring), so a service's unaddressed changes
    compound across cycles instead of the earlier ones being silently
    discarded.

See `algorand_shared.artifact_priority` for the priority sweep that
updates `priority`/`priority_computed_at` on these rows, and
`algorand_shared.to_compose_selection` for the day-ahead selection
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
    # Stable service_id for this artifact's underlying VENUE (the forum, the
    # xGov program, a YouTube channel, a Bluesky account) when `service_id`
    # itself is a synthetic per-item key (forum-topic:<id>,
    # xgov-proposal:<id>:<phase>, <channel>:<videoId>, <account>:<rkey>) that
    # can never literal-match a prior published article's service_id even
    # though the venue itself may already be well covered. None for any
    # artifact whose service_id IS its own venue (a plain web crawl diff, a
    # brief, an unlinked mail message). See _artifact_pool in
    # to_compose_selection.py, the only reader of this field.
    venue_service_id: str | None = None

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
    venue_service_id: str | None = None,
) -> tuple[str, bool]:
    """Insert a new pending artifact plus its content row. Returns (artifact_id, created).

    `venue_service_id`: for a per-item ingest lane (forum/xgov/youtube/
    bluesky -- see the `Artifact.venue_service_id` field docstring), the
    stable id of the underlying venue, distinct from this artifact's own
    per-item `service_id`. None for everything else. Purely a read signal
    for `to_compose_selection._artifact_pool`; never part of the dedup match
    below, which stays keyed on the literal per-item `service_id` exactly as
    before. When this call's own arg is falsy but an existing pending
    artifact is found and concatenated onto (below), that row's own
    venue_service_id is inherited rather than overwritten with None -- it
    identifies the service itself, not this one update, so it must never
    regress from set to unset partway through a chain of concatenation
    cycles.

    Dedup invariant: at most one PENDING artifact per service_id. When
    `service_id` is truthy and an existing pending artifact for it is found,
    that old artifact's ROW is superseded -- deleted from the pending index,
    marked DISCARDED -- but its CONTENT is never silently dropped: the new
    artifact's content is the old content plus this new content, concatenated
    (see `_concatenate_with_pending`), not the new content alone. This is a
    2026-08-26 change from the original replace-outright rule (mirroring
    publish_queue_store.enqueue_publish's identical rule): a service that
    gets small updates nobody's composed about yet should have its
    accumulated changes compound over successive ignored cycles -- 3 small
    unaddressed updates should read as more substantial (and score higher via
    word_count_score, which already scales with content length) than just the
    latest one, an organic pressure-release so a chronically-small-priority
    service isn't permanently stuck at a low score. `service_id` is
    frequently None (a brief, a mail message with no linked service) --
    those never dedup (and so never concatenate) against each other or
    anything else.

    `event_date`/`created_at` on the merged row are simply this call's own
    `now`/`event_date` (the NEWEST event) -- deliberately not widened into a
    range, so timeliness_score reflects how fresh the most recent activity
    is, while word_count_score (over the concatenated content) is what
    reflects the accumulation. `title` likewise stays the latest update's own
    title -- the concatenated body's own "Latest update" section still names
    the newest development, but the headline anchor a compose step would use
    should be about what's newest, not a merge of every accumulated title.
    """
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ArtifactStmts

    session = get_cassandra_session()
    now = now or datetime.now(tz=UTC)

    final_content = content
    final_metadata = dict(metadata or {})

    if service_id:
        for row in session.execute(ArtifactStmts.LIST_PENDING, (PENDING, 2000)):
            if row.service_id == service_id:
                old_content_row = session.execute(ArtifactStmts.GET_CONTENT, (row.artifact_id,)).one()
                if old_content_row is not None:
                    final_content, final_metadata = _concatenate_with_pending(
                        old_title=old_content_row.title or "",
                        old_content=old_content_row.content or "",
                        old_metadata_raw=old_content_row.metadata,
                        old_url=row.url,
                        old_event_date=row.event_date,
                        old_created_at=row.created_at,
                        new_title=title,
                        new_content=content,
                        new_metadata=metadata or {},
                    )
                # venue_service_id identifies the underlying VENUE, a stable
                # property of the service_id, not of any one update event --
                # unlike title (which deliberately always takes the latest),
                # this must never regress from set to unset just because one
                # particular call in a chain of concatenation cycles happened
                # not to supply it. Falls back to the row being superseded
                # here when this call's own arg is falsy. Matters most for
                # service_reconciliation.reconcile_duplicate_pending_artifacts,
                # whose multi-duplicate fold re-inserts each duplicate's own
                # (possibly inconsistent) venue_service_id in turn -- without
                # this fallback, a later fold step for a duplicate that never
                # got backfilled could silently wipe out an earlier one that
                # had already been recovered.
                venue_service_id = venue_service_id or (getattr(row, "venue_service_id", None) or None)
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
            venue_service_id,
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
        (
            PENDING,
            priority,
            now,
            artifact_id,
            service_id,
            venue_service_id,
            channel,
            url,
            event_date,
            None,
        ),
    )
    session.execute(
        ArtifactStmts.INSERT_CONTENT,
        (artifact_id, title, final_content, json.dumps(final_metadata, separators=(",", ":"))),
    )
    return str(artifact_id), True


# --------------------------------------------------------------------------- #
# Per-service artifact concatenation (2026-08-26) -- see insert_artifact's own
# docstring for the "why". Pure string/dict building, no I/O; kept separate so
# the concatenation shape can be unit-tested directly without a fake session.
# --------------------------------------------------------------------------- #

# Between the accumulated-old section and the new section. Deliberately
# legible as real prose structure (not a log-style delimiter): the writer/
# compose step downstream will eventually read this content directly, so a
# concatenated artifact needs to survive being handed straight to an LLM
# without looking like garbled runon text.
ARTIFACT_CONCAT_SEPARATOR = "\n\n---\n\n"


def _cap_old_content(old_content: str, max_chars: int) -> str:
    """Bound the ACCUMULATED-OLD portion of a concatenation to `max_chars` (ARTIFACT_CONCAT_MAX_OLD_CHARS), trimming from the FRONT (oldest material first) when it's exceeded -- the newest content handed to insert_artifact is never trimmed by this. See ARTIFACT_CONCAT_MAX_OLD_CHARS's own config comment for why this is a defensive ceiling, not a tuned knob, at this platform's volume."""
    if len(old_content) <= max_chars:
        return old_content
    truncated = old_content[-max_chars:]
    # Prefer cutting on a paragraph boundary near the start of what survives,
    # so the kept text doesn't open mid-sentence.
    idx = truncated.find("\n\n")
    if 0 <= idx < max_chars * 0.3:
        truncated = truncated[idx + 2 :]
    return "[earlier history truncated]\n\n" + truncated


def _concatenate_with_pending(
    *,
    old_title: str,
    old_content: str,
    old_metadata_raw: str | None,
    old_url: str | None,
    old_event_date: datetime | None,
    old_created_at: datetime | None,
    new_title: str,
    new_content: str,
    new_metadata: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Build the (content, metadata) for a new artifact superseding an existing pending one for the same service_id -- concatenation, not replacement.

    Content: the old artifact's content (capped -- see _cap_old_content) under
    an "Earlier update (not yet covered)" heading, then a "---" divider, then
    the new content under a "Latest update" heading. Framed as clearly-labeled
    prose sections (not a diff/log format) since a downstream compose step may
    eventually read this content directly.

    Metadata: `new_metadata`'s own top-level keys win outright (display_name/
    source_kind/payload/... all stay readable at the top level exactly
    where existing readers expect them, reflecting the LATEST signal --
    the correct one to mirror). The old artifact's full
    metadata (plus its title/url/event_date/created_at, which live outside
    metadata as separate columns) is appended as one entry to a
    metadata["segments"] list -- so nothing from prior cycles is lost, each
    concatenation just adds one more entry, and a service concatenated
    multiple times without composing builds a full provenance trail.
    """
    from app.core.config import ARTIFACT_CONCAT_MAX_OLD_CHARS

    capped_old_content = _cap_old_content(old_content, ARTIFACT_CONCAT_MAX_OLD_CHARS)
    old_label = old_title.strip() or "(untitled)"
    new_label = new_title.strip() or "(untitled)"
    merged_content = (
        f"### Earlier update (not yet covered): {old_label}\n\n{capped_old_content}"
        f"{ARTIFACT_CONCAT_SEPARATOR}"
        f"### Latest update: {new_label}\n\n{new_content}"
    )

    try:
        old_metadata = json.loads(old_metadata_raw or "{}")
    except json.JSONDecodeError:
        old_metadata = {}
    old_segments = list(old_metadata.pop("segments", None) or [])
    old_snapshot = {
        **old_metadata,
        "_title": old_title,
        "_url": old_url,
        "_event_date": old_event_date.isoformat() if old_event_date else None,
        "_created_at": old_created_at.isoformat() if old_created_at else None,
    }
    merged_metadata = dict(new_metadata)
    merged_metadata["segments"] = [*old_segments, old_snapshot]
    return merged_content, merged_metadata


def _delete_pending_row(
    session: object, *, status: str, priority: float, created_at: datetime, artifact_id: uuid.UUID
) -> None:
    from algorand_shared.artifact_statements import ArtifactStmts

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
        venue_service_id=getattr(row, "venue_service_id", None) or None,
    )


def list_pending_artifacts(*, limit: int = 2000) -> list[Artifact]:
    """Load pending artifacts from the pending index, priority DESC, created_at ASC (the index's own clustering order)."""
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ArtifactStmts

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
            venue_service_id=getattr(row, "venue_service_id", None) or None,
        )
        for row in session.execute(ArtifactStmts.LIST_PENDING, (PENDING, limit))
    ]


def get_artifact(artifact_id: str) -> Artifact | None:
    """Load one artifact by id regardless of status."""
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ArtifactStmts

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

    from algorand_shared.artifact_statements import ArtifactStmts

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

    from algorand_shared.artifact_statements import ArtifactStmts

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
                getattr(full, "venue_service_id", None) if full else None,
                full.channel if full else None,
                full.url if full else None,
                full.event_date if full else None,
                full.human_pick_day if full else None,
            ),
        )


def mark_artifact_status(artifact_id: str, status: str) -> None:
    """Move an artifact out of (or within) the pending lane: selected / composed / discarded. Removes its pending-index row whenever the new status isn't "pending"."""
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ArtifactStmts

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


def revert_artifact_to_pending(artifact_id: str) -> bool:
    """Move an artifact back into the pending lane -- the reverse of mark_artifact_status's pending -> non-pending transition (re-adds the artifacts_pending index row mark_artifact_status would have removed). Used by to_compose_selection.reset_to_compose_for_day to undo a SELECTED artifact's status flip when an admin redoes a day's to_compose picks.

    Guarded to only ever move an artifact OUT of SELECTED: reverting a
    COMPOSED artifact (already turned into a real article by drain_to_compose)
    or a DISCARDED one (a pre-compose gate permanently dropped it) back to
    pending would silently resurrect work that has already moved past
    selection -- never what a "redo the picks" admin action should do.
    Returns False (no-op, nothing mutated) for an unknown id or any status
    other than SELECTED; True when it actually reverted.
    """
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ArtifactStmts

    try:
        aid = uuid.UUID(str(artifact_id))
    except ValueError:
        return False
    session = get_cassandra_session()
    status_row = session.execute(ArtifactStmts.GET_STATUS_ROW, (aid,)).one()
    if status_row is None or status_row.status != SELECTED:
        return False

    full = session.execute(ArtifactStmts.GET, (aid,)).one()
    session.execute(ArtifactStmts.UPDATE_STATUS, (PENDING, aid))
    session.execute(
        ArtifactStmts.INSERT_PENDING,
        (
            PENDING,
            status_row.priority,
            status_row.created_at,
            aid,
            full.service_id if full else None,
            full.venue_service_id if full else None,
            full.channel if full else None,
            full.url if full else None,
            full.event_date if full else None,
            full.human_pick_day if full else None,
        ),
    )
    return True


def _set_pending_index_pin(
    session: object, aid: uuid.UUID, status_row: object, day: str | None
) -> None:
    """Mirror a human_pick_day change onto the pending-index row, when the artifact is currently pending. human_pick_day isn't part of that table's primary key, so this is a plain UPDATE (unlike the priority delete+reinsert dance)."""
    from algorand_shared.artifact_statements import ArtifactStmts

    if status_row.status == PENDING and status_row.created_at is not None:
        session.execute(
            ArtifactStmts.SET_PENDING_HUMAN_PICK,
            (day, PENDING, status_row.priority, status_row.created_at, aid),
        )


def pin_artifact_for_day(artifact_id: str, day: str) -> bool:
    """Admin-facing hook: pin an artifact as tomorrow's (or any future day's) human pick. Returns False for an unknown id."""
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ArtifactStmts

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
    """Clear a spent (or superseded) human pin."""
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ArtifactStmts

    try:
        aid = uuid.UUID(str(artifact_id))
    except ValueError:
        return
    session = get_cassandra_session()
    status_row = session.execute(ArtifactStmts.GET_STATUS_ROW, (aid,)).one()
    session.execute(ArtifactStmts.CLEAR_HUMAN_PICK, (aid,))
    if status_row is not None:
        _set_pending_index_pin(session, aid, status_row, None)


def set_artifact_venue_service_id(artifact_id: str, venue_service_id: str) -> bool:
    """Backfill `venue_service_id` on an existing artifact -- the write side of the ongoing bug-class-2 reconciliation sweep (see `service_reconciliation.backfill_missing_venue_service_ids`), a safety net for a per-item artifact that landed without one (inserted before the lane fix deployed, or by a future lane that forgets to pass it). Mirrors pin_artifact_for_day's shape: update `artifacts`, then mirror onto the pending-index row when still pending (not part of that table's key, so a plain UPDATE). Returns False for an unknown id."""
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ArtifactStmts

    try:
        aid = uuid.UUID(str(artifact_id))
    except ValueError:
        return False
    session = get_cassandra_session()
    status_row = session.execute(ArtifactStmts.GET_STATUS_ROW, (aid,)).one()
    if status_row is None:
        return False
    session.execute(ArtifactStmts.SET_VENUE_SERVICE_ID, (venue_service_id, aid))
    if status_row.status == PENDING and status_row.created_at is not None:
        session.execute(
            ArtifactStmts.SET_PENDING_VENUE_SERVICE_ID,
            (venue_service_id, PENDING, status_row.priority, status_row.created_at, aid),
        )
    return True


def get_artifact_detail(artifact_id: str) -> dict[str, Any] | None:
    """Full title/content/url/metadata for one artifact, assembled from get_artifact + get_artifact_content -- what would actually get fed to the writer/composer. Shared by backend's admin_get_artifact_content route and workers' get_artifact_detail Celery task (still used by other worker-side callers), so both return the exact same shape.

    Returns None for an unknown OR malformed artifact_id (both get_artifact
    and get_artifact_content already fail closed to None on a bad uuid).
    """
    artifact = get_artifact(artifact_id)
    content = get_artifact_content(artifact_id)
    if artifact is None and content is None:
        return None
    return {
        "artifact_id": artifact_id,
        "title": content.title if content else "",
        "content": content.content if content else "",
        "metadata": content.metadata if content else {},
        "service_id": artifact.service_id if artifact else None,
        "url": artifact.url if artifact else None,
        "channel": artifact.channel if artifact else "",
        "status": artifact.status if artifact else "",
    }
