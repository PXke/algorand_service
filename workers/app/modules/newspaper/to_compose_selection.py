"""Day-ahead compose selection for the editorial-room `to_compose` table.

LIVE (2026-08-25): `select_to_compose_for_day` is called once daily by
`app.modules.newspaper.tasks.queue_drain_tasks.select_to_compose_for_today_task`
(a beat), and `drain_to_compose` (a tighter-cadence beat in the same module)
composes from its output. `preview_to_compose_for_day` remains the read-only
admin-dashboard forecast, called directly (not on a beat). See
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


def _rank_platform_picks(
    pending: list[Artifact], *, human_pick: Artifact | None, platform_n: int
) -> list[Artifact]:
    """Shared platform-fill ranking: top-priority PENDING artifacts (in `pending`'s own order), deduped by service_id, excluding the human pick's own artifact/service. Pure -- no Cassandra writes, no status mutation -- shared by select_to_compose_for_day (which then persists it) and preview_to_compose_for_day (which doesn't)."""
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
    return platform_picks


def select_to_compose_for_day(day: str, *, now: datetime | None = None) -> dict[str, object]:
    """Select `day`'s compose lineup: one human slot (only when pinned -- otherwise left EMPTY, no platform backfill, an explicit owner decision against overcomposing to compensate) plus N-1 platform slots (N = NEWS_MAX_ARTICLES_PER_DAY) filled by the top-priority PENDING artifacts, respecting the 1-pending-per-service dedup and excluding whatever the human already picked.

    Idempotency note: re-running this for a `day` that already has rows
    clears the to_compose rows first, but an artifact this function already
    moved to 'selected' on an earlier run for the SAME day stays 'selected'
    (no longer 'pending') and won't be reselected -- call at most once per
    day in practice, matching the real system's intended once-a-day cadence.

    WRITES: clears/repopulates `to_compose` for `day` and moves every picked
    artifact pending -> selected. See preview_to_compose_for_day for a
    read-only version of this same ranking, safe to call on every admin page
    load.
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
    platform_picks = _rank_platform_picks(pending, human_pick=human_pick, platform_n=platform_n)

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


def preview_to_compose_for_day(day: str) -> dict[str, object]:
    """Read-only preview of what select_to_compose_for_day(day) currently would pick -- the query behind the admin shadow-selection dashboard.

    NEVER mutates artifact status and NEVER touches `to_compose` -- unlike
    select_to_compose_for_day, this is safe to call on every page load /
    poll. Uses the exact same ranking (_rank_platform_picks over
    list_pending_artifacts()'s own priority-DESC order) so "what would be
    picked" here matches what a real selection run would pick right now.

    Returns every PENDING artifact (not just the selected ones), each with
    a freshly recomputed priority breakdown (word_count/timeliness/
    ecosystem_listed -- the same pure functions the daily sweep uses) and
    which lane (if any) this preview would put it in. The recomputed total
    can drift slightly above/below the artifact's stored `priority` between
    sweeps (the sweep runs roughly every 24h; timeliness decays continuously)
    -- that's intentional, showing the score as of right now rather than as
    of the last sweep. Ordering, however, follows list_pending_artifacts()'s
    stored-priority order, matching what a real (unmutated) selection run
    would actually pick, not this preview's own live recompute.
    """
    from app.core import config as cfg
    from app.modules.newspaper.artifact_priority import (
        ecosystem_listed_score,
        timeliness_score,
        word_count_score,
    )
    from app.modules.newspaper.artifact_store import get_artifact_content

    pending = list_pending_artifacts()
    human_pick = next((a for a in pending if a.human_pick_day == day), None)

    platform_n = max(0, cfg.NEWS_MAX_ARTICLES_PER_DAY - 1)
    platform_picks = _rank_platform_picks(pending, human_pick=human_pick, platform_n=platform_n)
    platform_ids = {a.artifact_id for a in platform_picks}

    items: list[dict[str, object]] = []
    for artifact in pending:
        content = get_artifact_content(artifact.artifact_id)
        breakdown = {
            "word_count": word_count_score(content.content if content else ""),
            "timeliness": timeliness_score(artifact.event_date, artifact.created_at),
            "ecosystem_listed": ecosystem_listed_score(artifact.url),
        }
        if human_pick is not None and artifact.artifact_id == human_pick.artifact_id:
            lane: str | None = "human"
        elif artifact.artifact_id in platform_ids:
            lane = "platform"
        else:
            lane = None
        items.append(
            {
                "artifact_id": artifact.artifact_id,
                "service_id": artifact.service_id,
                "url": artifact.url,
                "channel": artifact.channel,
                "title": content.title if content else "",
                "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
                "event_date": artifact.event_date.isoformat() if artifact.event_date else None,
                "priority": round(sum(breakdown.values()), 4),
                "priority_breakdown": breakdown,
                "human_pick_day": artifact.human_pick_day,
                "is_pinned_for_day": artifact.human_pick_day == day,
                "selected_lane": lane,
            }
        )

    return {
        "status": "ok",
        "compose_day": day,
        "human_picked": human_pick is not None,
        "platform_slots_filled": len(platform_picks),
        "platform_slots_available": platform_n,
        "items": items,
    }
