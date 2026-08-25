"""Day-ahead compose selection for the editorial-room `to_compose` table (2026-08-25, SHADOW MODE).

NOT wired to any live compose trigger yet -- a plain, directly-callable
function (plus an admin-facing pin hook) that a future admin UI / beat task
can call, per this phase's explicit scope ("doesn't need to be a live beat
task yet since nothing consumes to_compose for real composing"). See
artifact_store.py for the human-pin mechanism and artifact_priority.py for
the priority this reads.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from app.modules.newspaper.artifact_store import (
    SELECTED,
    Artifact,
    list_pending_artifacts,
    mark_artifact_status,
    pin_artifact_for_day,
)


def pin_for_tomorrow(artifact_id: str, *, today: date | None = None) -> bool:
    """Admin-facing convenience: pin an artifact as the human pick for the compose day immediately after `today` (default: the real today). Thin wrapper around artifact_store.pin_artifact_for_day with the day-ahead date math applied -- the hook a future "pin this for tomorrow" admin action would call."""
    today = today or datetime.now(tz=UTC).date()
    tomorrow = today + timedelta(days=1)
    return pin_artifact_for_day(artifact_id, tomorrow.isoformat())


def select_to_compose_for_day(day: str, *, now: datetime | None = None) -> dict[str, object]:
    """Select `day`'s compose lineup: one human slot (only when pinned -- otherwise left EMPTY, no platform backfill, an explicit owner decision against overcomposing to compensate) plus N-1 platform slots (N = NEWS_MAX_ARTICLES_PER_DAY) filled by the top-priority PENDING artifacts, respecting the 1-pending-per-service dedup and excluding whatever the human already picked.

    Idempotency note: re-running this for a `day` that already has rows
    clears the to_compose rows first, but an artifact this function already
    moved to 'selected' on an earlier run for the SAME day stays 'selected'
    (no longer 'pending') and won't be reselected -- call at most once per
    day in practice, matching the real system's intended once-a-day cadence.
    """
    from app.core import config as cfg
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ToComposeStmts

    now = now or datetime.now(tz=UTC)
    session = get_cassandra_session()
    session.execute(ToComposeStmts.DELETE_FOR_DAY, (day,))

    # Already priority DESC, created_at ASC (the pending index's own
    # clustering order) -- no extra sort needed for the platform fill below.
    pending = list_pending_artifacts()

    human_pick = next((a for a in pending if a.human_pick_day == day), None)

    platform_n = max(0, cfg.NEWS_MAX_ARTICLES_PER_DAY - 1)
    excluded_service = human_pick.service_id if human_pick and human_pick.service_id else None
    seen_services: set[str] = set()
    platform_picks: list[Artifact] = []
    for artifact in pending:
        if len(platform_picks) >= platform_n:
            break
        if human_pick is not None and artifact.artifact_id == human_pick.artifact_id:
            continue
        if artifact.service_id:
            if artifact.service_id == excluded_service or artifact.service_id in seen_services:
                continue
            seen_services.add(artifact.service_id)
        platform_picks.append(artifact)

    slot = 0
    selections: list[dict[str, object]] = []

    if human_pick is not None:
        _insert_slot(session, day=day, slot=slot, artifact=human_pick, lane="human", now=now)
        selections.append(
            {"slot": slot, "lane": "human", "artifact_id": human_pick.artifact_id}
        )
        slot += 1

    for artifact in platform_picks:
        _insert_slot(session, day=day, slot=slot, artifact=artifact, lane="platform", now=now)
        selections.append(
            {"slot": slot, "lane": "platform", "artifact_id": artifact.artifact_id}
        )
        slot += 1

    return {
        "status": "ok",
        "compose_day": day,
        "human_picked": human_pick is not None,
        "platform_slots_filled": len(platform_picks),
        "platform_slots_available": platform_n,
        "selections": selections,
    }


def _insert_slot(
    session: object, *, day: str, slot: int, artifact: Artifact, lane: str, now: datetime
) -> None:
    from app.core.statements import ToComposeStmts

    session.execute(  # type: ignore[attr-defined]
        ToComposeStmts.INSERT,
        (day, slot, uuid.UUID(artifact.artifact_id), lane, artifact.service_id, now),
    )
    mark_artifact_status(artifact.artifact_id, SELECTED)


def list_to_compose_for_day(day: str) -> list[dict[str, object]]:
    """Read back the selected lineup for a compose day, slot-ordered (for tests / a future admin view)."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ToComposeStmts

    session = get_cassandra_session()
    out = [
        {
            "slot": row.slot,
            "artifact_id": str(row.artifact_id),
            "lane": row.lane,
            "service_id": row.service_id,
            "picked_at": row.picked_at,
        }
        for row in session.execute(ToComposeStmts.LIST_FOR_DAY, (day,))
    ]
    return sorted(out, key=lambda r: r["slot"])
