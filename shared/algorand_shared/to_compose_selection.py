"""Day-ahead compose selection for the editorial-room `to_compose` table.

Moved here from `workers/app/modules/newspaper/to_compose_selection.py`
(2026-08-26) alongside `artifact_store.py` / `artifact_priority.py`, so
backend's admin artifacts routes (to-compose-selected, to-compose-preview,
to-compose-reset, pin-for-tomorrow) can call this logic directly instead of a
synchronous Celery round-trip into a worker process -- the round-trip was
found to be the sole reason those purely-read-only (or, for pin/reset,
lightweight-write) routes could time out when an unrelated heavy job filled
the shared Celery queue. Workers' own beats
(`queue_drain_tasks.select_to_compose_for_today_task`,
`tasks/artifact_tasks.py`'s five on-demand tasks) import this module's
functions from here now.

LIVE (2026-08-25): `select_to_compose_for_day` is called once daily by
`app.modules.newspaper.tasks.queue_drain_tasks.select_to_compose_for_today_task`
(a beat), and `drain_to_compose` (a tighter-cadence beat in the same module)
composes from its output. `preview_to_compose_for_day` remains the read-only
admin-dashboard forecast, now called directly by the admin route instead of
via Celery. See artifact_store.py for the human-pin mechanism and
artifact_priority.py for the priority this reads.

2026-08-26: platform slots are no longer filled from one undifferentiated
priority-ranked pool. They're split into a NEW_SERVICE_POOL (services this
platform has never composed/published before) and an UPDATE_POOL (services
already covered at least once), each with a guaranteed minimum floor -- see
ARTIFACT_NEW_SERVICE_MIN_SHARE and _rank_platform_picks. This protects
against a large, frequently-updating service saturating every platform slot
with routine small updates and crowding out first-ever coverage of smaller/
newer services, while still letting a genuinely big update from an
established service win purely on priority when there's surplus capacity.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from algorand_shared.artifact_store import (
    SELECTED,
    Artifact,
    list_pending_artifacts,
    mark_artifact_status,
    pin_artifact_for_day,
)

# Platform-pick pool labels -- see _artifact_pool / _rank_platform_picks.
NEW_SERVICE_POOL = "new_service"
UPDATE_POOL = "update"


def pin_for_tomorrow(artifact_id: str, *, today: date | None = None) -> bool:
    """Admin-facing convenience: pin an artifact as the human pick for the compose day immediately after `today` (default: the real today). Thin wrapper around artifact_store.pin_artifact_for_day with the day-ahead date math applied -- the hook a future "pin this for tomorrow" admin action would call."""
    today = today or datetime.now(tz=UTC).date()
    tomorrow = today + timedelta(days=1)
    return pin_artifact_for_day(artifact_id, tomorrow.isoformat())


def _artifact_pool(artifact: Artifact, *, cache: dict[str, str]) -> str:
    """Which selection pool this artifact's own service (or VENUE) belongs to: NEW_SERVICE_POOL when the platform has never composed/published an article for that id before, else UPDATE_POOL.

    Reuses `article_matching.service_has_article` -- the existing "has this
    service ever had a real published article" signal (a direct query
    against `articles`, not a new registry) -- rather than inventing a
    second one. An artifact with no service_id (a brief/mail with nothing to
    protect against big-actor saturation from) is always UPDATE_POOL, which
    falls out of service_has_article's own fails-open rule for an empty id
    (returns True/"already covered" immediately, no query).

    Checks `artifact.venue_service_id or artifact.service_id`, NOT
    `service_id` alone (2026-08-2x fix): several ingest lanes (forum hot-
    topics, xGov proposal phases, YouTube videos, Bluesky posts) mint a
    synthetic service_id PER ITEM (e.g. "forum-topic:15288"), which can
    never literal-match a prior published article's service_id even when
    the underlying VENUE (the forum, the xGov program, the channel, the
    account) is well covered -- every item from those lanes would otherwise
    permanently occupy the guaranteed NEW_SERVICE_POOL floor instead of
    competing in UPDATE_POOL like the routine coverage it actually is. This
    intentionally does NOT touch `_rank_platform_picks`'s own per-service
    dedup/exclusion, which stays keyed on the literal `service_id` -- two
    artifacts about the SAME venue (e.g. two different forum threads) must
    still be treated as two distinct dedup candidates, only their POOL
    classification should follow the venue.

    `cache` memoizes by this same key within one selection/preview pass --
    the per-service pending dedup means each service_id appears at most once
    in a `pending` list anyway, but a preview pass calls this once per
    pending item AND once more inside _rank_platform_picks, so the cache
    avoids a second Cassandra round-trip for the same service/venue.
    """
    from algorand_shared.article_matching import service_has_article

    key = artifact.venue_service_id or artifact.service_id or ""
    if key in cache:
        return cache[key]
    pool = UPDATE_POOL if service_has_article(key) else NEW_SERVICE_POOL
    cache[key] = pool
    return pool


def _rank_platform_picks(
    pending: list[Artifact],
    *,
    human_pick: Artifact | None,
    platform_n: int,
    pool_cache: dict[str, str] | None = None,
) -> list[Artifact]:
    """Shared platform-fill ranking: top-priority PENDING artifacts (in `pending`'s own order), deduped by service_id, excluding the human pick's own artifact/service.

    2026-08-26: no longer one undifferentiated priority-ranked pool. Eligible
    candidates (post dedup/exclusion) split into NEW_SERVICE_POOL and
    UPDATE_POOL (see _artifact_pool), each with its own guaranteed MINIMUM
    floor of platform_n slots:

        new_floor    = floor(platform_n * ARTIFACT_NEW_SERVICE_MIN_SHARE)
        update_floor = floor(platform_n * (1 - ARTIFACT_NEW_SERVICE_MIN_SHARE))

    Floors are a MINIMUM guarantee, not a rigid partition. Whatever's left
    after both floors are filled -- because platform_n doesn't divide evenly
    (floors are floored, so up to 1 slot is always left over), or because one
    pool didn't have enough eligible candidates to fill its own floor -- goes
    to the next-highest-priority remaining candidate from EITHER pool,
    pooled together. This means: (a) a platform slot is never left empty for
    lack of candidates in one pool (a thin pool gets backfilled from the
    other), and (b) a single exceptionally strong artifact from either pool
    can still win a slot beyond its own pool's floor once both floors are
    already met -- an explicit owner requirement that a genuinely big update
    from an established (already-covered) service should still be able to
    win on priority alone.

    The final returned order is priority order (matching `pending`'s own
    order) regardless of which pool a pick came from -- pool membership only
    affects WHICH artifacts get a slot, never the slot ordering of the ones
    that do.

    Pure -- no Cassandra writes, no status mutation (though _artifact_pool
    does read `articles` per distinct service_id) -- shared by
    select_to_compose_for_day (which then persists it) and
    preview_to_compose_for_day (which doesn't).
    """
    from app.core import config as cfg

    excluded_service = human_pick.service_id if human_pick and human_pick.service_id else None
    seen_services: set[str] = set()
    eligible: list[Artifact] = []
    for artifact in pending:
        if human_pick is not None and artifact.artifact_id == human_pick.artifact_id:
            continue
        if artifact.service_id:
            if artifact.service_id == excluded_service or artifact.service_id in seen_services:
                continue
            seen_services.add(artifact.service_id)
        eligible.append(artifact)

    cache = pool_cache if pool_cache is not None else {}
    new_pool = [a for a in eligible if _artifact_pool(a, cache=cache) == NEW_SERVICE_POOL]
    update_pool = [a for a in eligible if _artifact_pool(a, cache=cache) == UPDATE_POOL]

    share = cfg.ARTIFACT_NEW_SERVICE_MIN_SHARE
    # +1e-9 guards against a floor computation landing just under an exact
    # integer boundary from float error (e.g. platform_n=4, share=0.5 must
    # floor to 2, never 1.999999999 -> 1).
    new_floor = int(platform_n * share + 1e-9)
    update_floor = int(platform_n * (1 - share) + 1e-9)

    new_take = new_pool[:new_floor]
    update_take = update_pool[:update_floor]
    chosen_ids = {a.artifact_id for a in new_take} | {a.artifact_id for a in update_take}

    remaining_needed = platform_n - len(chosen_ids)
    if remaining_needed > 0:
        leftover = [a for a in eligible if a.artifact_id not in chosen_ids]
        chosen_ids |= {a.artifact_id for a in leftover[:remaining_needed]}

    return [a for a in eligible if a.artifact_id in chosen_ids][:platform_n]


def select_to_compose_for_day(day: str, *, now: datetime | None = None) -> dict[str, object]:
    """Select `day`'s compose lineup: one human slot (only when pinned -- otherwise left EMPTY, no platform backfill, an explicit owner decision against overcomposing to compensate) plus N-1 platform slots (N = NEWS_MAX_ARTICLES_PER_DAY) filled by the top-priority PENDING artifacts, respecting the 1-pending-per-service dedup, excluding whatever the human already picked, and the new-service-vs-update pool floors (see _rank_platform_picks).

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

    from algorand_shared.artifact_statements import ToComposeStmts

    now = now or datetime.now(tz=UTC)
    session = get_cassandra_session()
    session.execute(ToComposeStmts.DELETE_FOR_DAY, (day,))

    # Already priority DESC, created_at ASC (the pending index's own
    # clustering order) -- no extra sort needed for the platform fill below.
    pending = list_pending_artifacts()

    human_pick = next((a for a in pending if a.human_pick_day == day), None)

    platform_n = max(0, cfg.NEWS_MAX_ARTICLES_PER_DAY - 1)
    pool_cache: dict[str, str] = {}
    platform_picks = _rank_platform_picks(
        pending, human_pick=human_pick, platform_n=platform_n, pool_cache=pool_cache
    )

    slot = 0
    selections: list[dict[str, object]] = []
    pool_counts = {NEW_SERVICE_POOL: 0, UPDATE_POOL: 0}

    if human_pick is not None:
        _insert_slot(session, day=day, slot=slot, artifact=human_pick, lane="human", now=now)
        selections.append(
            {"slot": slot, "lane": "human", "artifact_id": human_pick.artifact_id, "pool": None}
        )
        slot += 1

    for artifact in platform_picks:
        _insert_slot(session, day=day, slot=slot, artifact=artifact, lane="platform", now=now)
        pool = _artifact_pool(artifact, cache=pool_cache)
        pool_counts[pool] += 1
        selections.append(
            {"slot": slot, "lane": "platform", "artifact_id": artifact.artifact_id, "pool": pool}
        )
        slot += 1

    return {
        "status": "ok",
        "compose_day": day,
        "human_picked": human_pick is not None,
        "platform_slots_filled": len(platform_picks),
        "platform_slots_available": platform_n,
        "platform_pool_counts": pool_counts,
        "selections": selections,
    }


def _purge_other_to_compose_rows(session: object, artifact_id: str) -> None:
    """Delete every `to_compose` row referencing `artifact_id` on a day OTHER than the one about to be written.

    Root-caused 2026-08-27 (live prod, two separate artifacts corrupted by
    this): an artifact that goes unselected for a day stays a normal
    PENDING candidate, so it can legitimately be picked again on a LATER
    day. Nothing ever cleaned up its OLD day's `to_compose` row when that
    happened -- so the same artifact_id ends up referenced by to_compose
    rows on MULTIPLE different days at once (one current/valid, one stale).
    `reclaim_stale_selected_artifacts` then finds the stale OLD row, sees
    the artifact's (legitimately, currently) SELECTED status, and reverts
    it wholesale -- silently breaking the CURRENT day's real selection it
    had nothing to do with. Purging every other day's row for this
    artifact_id at the moment it's freshly selected removes the aliasing
    at its source, rather than trying to detect/repair it after the fact.
    """
    from algorand_shared.artifact_statements import ToComposeStmts

    for row in session.execute(ToComposeStmts.LIST_ALL):  # type: ignore[attr-defined]
        if str(row.artifact_id) == artifact_id:
            session.execute(ToComposeStmts.DELETE_SLOT, (row.compose_day, row.slot))  # type: ignore[attr-defined]


def _insert_slot(
    session: object, *, day: str, slot: int, artifact: Artifact, lane: str, now: datetime
) -> None:
    from algorand_shared.artifact_statements import ToComposeStmts

    _purge_other_to_compose_rows(session, artifact.artifact_id)
    session.execute(  # type: ignore[attr-defined]
        ToComposeStmts.INSERT,
        (day, slot, uuid.UUID(artifact.artifact_id), lane, artifact.service_id, now),
    )
    mark_artifact_status(artifact.artifact_id, SELECTED)


def list_to_compose_for_day(day: str) -> list[dict[str, object]]:
    """Read back the REAL, persisted selection for a compose day, slot-ordered -- what select_to_compose_for_day(day) actually picked the last time it ran (empty until then), not a forecast. Used directly in tests and by the admin dashboard's "selected for tomorrow" section.

    `picked_at` is isoformatted (not a raw datetime) so this is safe to hand
    straight to a JSON-serializing transport.
    """
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ToComposeStmts

    session = get_cassandra_session()
    out = [
        {
            "slot": row.slot,
            "artifact_id": str(row.artifact_id),
            "lane": row.lane,
            "service_id": row.service_id,
            "picked_at": row.picked_at.isoformat() if row.picked_at else None,
        }
        for row in session.execute(ToComposeStmts.LIST_FOR_DAY, (day,))
    ]
    return sorted(out, key=lambda r: r["slot"])


def reset_to_compose_for_day(day: str) -> dict[str, object]:
    """Admin-facing "redo the picks" building block: clear `day`'s locked-in `to_compose` selection and revert any artifact it selected back to PENDING, so a follow-up select_to_compose_for_day(day) call is free to re-pick from the full widened pool again.

    Guard (the same one revert_artifact_to_pending itself enforces): only an
    artifact still in SELECTED status is reverted. One that has since
    progressed to COMPOSED (drain_to_compose already turned it into a real
    article) or DISCARDED (a pre-compose gate permanently dropped it) is left
    completely alone -- re-running selection can never un-publish or
    un-discard those, and this function must never silently pretend it did.
    Each such artifact is reported in `skipped` (with its actual current
    status) rather than just vanishing from the count, so an admin UI can say
    "N of these picks had already moved on and were left as-is" instead of
    claiming a full clean reset that didn't actually happen.

    WRITES: deletes every to_compose row for `day` and reverts each
    still-SELECTED artifact's status back to pending (re-adding it to the
    pending index). Does NOT re-run selection itself -- see
    reset_and_reselect_for_day for the one-button combination the admin
    "Redo today's picks" route actually calls.
    """
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ToComposeStmts
    from algorand_shared.artifact_store import get_artifact, revert_artifact_to_pending

    rows = list_to_compose_for_day(day)

    reverted: list[str] = []
    skipped: list[dict[str, str]] = []
    for row in rows:
        artifact_id = str(row["artifact_id"])
        artifact = get_artifact(artifact_id)
        status = artifact.status if artifact is not None else "unknown"
        if status == SELECTED and revert_artifact_to_pending(artifact_id):
            reverted.append(artifact_id)
        else:
            skipped.append({"artifact_id": artifact_id, "status": status})

    session = get_cassandra_session()
    session.execute(ToComposeStmts.DELETE_FOR_DAY, (day,))

    return {
        "status": "ok",
        "compose_day": day,
        "cleared_slots": len(rows),
        "reverted_to_pending": reverted,
        "skipped": skipped,
        # Convenience flag: True only when every cleared slot's artifact was
        # still SELECTED and got reverted -- False means at least one pick
        # had already progressed (composed/discarded) and was left alone,
        # which the admin UI should surface rather than silently swallow.
        "fully_reverted": len(skipped) == 0,
    }


def reset_and_reselect_for_day(day: str, *, now: datetime | None = None) -> dict[str, object]:
    """One-button admin "redo today's picks" action: reset_to_compose_for_day(day) immediately followed by a fresh select_to_compose_for_day(day) over the now-widened pending pool. This is the function the admin "Redo today's picks" route actually calls -- see each half's own docstring for exactly what it does and (for the reset half) its already-progressed-artifact guard."""
    reset_result = reset_to_compose_for_day(day)
    selection_result = select_to_compose_for_day(day, now=now)
    return {
        "status": "ok",
        "compose_day": day,
        "reset": reset_result,
        "selection": selection_result,
    }


def find_stale_selected_artifacts(*, today: str | None = None) -> list[dict[str, object]]:
    """Read-only: every `to_compose` row for a day strictly BEFORE `today` whose artifact is still SELECTED -- a slot `drain_to_compose` never got to before its own day rolled over, and that nothing else in this module ever revisits on its own.

    Root-caused live 2026-08-26: `select_to_compose_for_day` moves a picked
    artifact PENDING -> SELECTED the moment it's chosen (`_insert_slot` ->
    `mark_artifact_status`), for BOTH the human pick and every platform pick
    alike -- there is no lane-specific exception here. `drain_to_compose`
    only ever composes `list_to_compose_for_day(today)` -- literally
    TODAY's own slate, every run, forever. If a slot stays SELECTED past
    midnight (a gate cooldown that never cleared, review_queue_full all
    day, a soft-time-limit interruption near end of day, or simply too many
    slots for the compose budget that day), its artifact is now invisible
    to EVERY future day: `list_pending_artifacts()` only returns
    status=PENDING rows, so it can never be re-picked, and no future
    `drain_to_compose` run ever looks at a day other than its own `today`.
    The content is silently stranded -- not published, not discarded, not
    reconsidered, just permanently SELECTED. Confirmed two real real-world
    casualties from 2026-08-25's platform picks (`algorand-co`,
    `forum-algorand-co`) sitting exactly like this before this function was
    written.

    Read-only, no writes -- pairs with `reclaim_stale_selected_artifacts`
    (dry_run=True by default there too) the same way this module's other
    report/act pairs work. `to_compose` has no per-day index to query
    against, so this does a full-table scan (`ToComposeStmts.LIST_ALL`) --
    safe, since the table holds at most a handful of rows per day, forever.

    Each finding also carries `has_current_reference`: True when the SAME
    artifact_id also has a `to_compose` row for a day >= `today` (root-
    caused live 2026-08-27: the same artifact can legitimately be re-picked
    on a LATER day after going unselected once, and nothing before
    `_insert_slot`'s own 2026-08-27 fix ever cleaned up its OLD day's row
    when that happened -- so a genuinely-current, correctly-SELECTED
    artifact could ALSO still be referenced by a stale old row here. Acting
    on such a finding must delete the stale row without touching the
    artifact's status -- see `reclaim_stale_selected_artifacts`.
    """
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ToComposeStmts
    from algorand_shared.artifact_store import get_artifact

    today = today or datetime.now(tz=UTC).date().isoformat()
    session = get_cassandra_session()

    all_rows = list(session.execute(ToComposeStmts.LIST_ALL))
    current_artifact_ids = {str(row.artifact_id) for row in all_rows if row.compose_day >= today}

    stale: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in all_rows:
        if row.compose_day >= today:
            continue
        artifact_id = str(row.artifact_id)
        if artifact_id in seen:
            continue
        artifact = get_artifact(artifact_id)
        if artifact is not None and artifact.status == SELECTED:
            seen.add(artifact_id)
            stale.append(
                {
                    "artifact_id": artifact_id,
                    "compose_day": row.compose_day,
                    "slot": row.slot,
                    "lane": row.lane,
                    "service_id": row.service_id,
                    "has_current_reference": artifact_id in current_artifact_ids,
                }
            )
    return stale


def reclaim_stale_selected_artifacts(
    *, today: str | None = None, dry_run: bool = True
) -> dict[str, object]:
    """Revert every stale-selected artifact (see `find_stale_selected_artifacts`) back to PENDING, re-entering it into the normal priority-ranked pool so a future `select_to_compose_for_day` can pick it up again like any other candidate -- not forced to the front, just no longer permanently stuck.

    Uses the existing `revert_artifact_to_pending` (built for the admin
    "redo picks" action, `reset_to_compose_for_day` above) rather than a new
    write path. A reclaimed HUMAN-lane pick also has its stale
    `human_pick_day` cleared (`clear_artifact_pin`) -- otherwise the
    artifact goes back to PENDING still carrying a human_pick_day for a day
    that has already passed, which is inert (no future day's exact-date
    match will ever equal it) but confusing leftover state to see on an
    otherwise-ordinary pending artifact.

    2026-08-26: also deletes the stale slot's own `to_compose` row
    (`ToComposeStmts.DELETE_SLOT`, keyed on the exact (compose_day, slot)
    primary key) once the revert succeeds -- root-caused live: the admin
    "Selected for today" view reads `to_compose` directly, so leaving that
    historical row in place made an already-reclaimed (back-to-pending)
    artifact keep showing as if still locked in for a day that will now
    never compose it. Deliberately per-slot, not `DELETE_FOR_DAY` -- must
    never touch any OTHER slot on that same day (a still-genuinely-selected
    pick, or another day's own historical record).

    2026-08-27: a finding with `has_current_reference=True` is handled
    differently -- the stale row is deleted (still genuinely garbage), but
    the artifact's status is left completely alone. Root-caused live: the
    SAME artifact_id had been legitimately re-selected for a CURRENT day
    via its own valid `to_compose` row, while an OLD day's row (never
    cleaned up before `_insert_slot`'s matching 2026-08-27 fix) still
    pointed at it too. Reverting the artifact's status here -- as this
    function used to do unconditionally -- silently broke that other,
    genuinely-current selection; two real prod artifacts were corrupted
    this way the same night this was found.

    `dry_run=True` by default, mirroring every other act-on-live-data
    function in this codebase added this session (`gray_zone_
    reconciliation.dispatch_gray_zone_deep_classify`, `browser_reaper.
    reap_orphaned_browser_processes`) -- reports what WOULD be reclaimed
    without writing anything until a caller opts in.
    """
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.artifact_statements import ToComposeStmts
    from algorand_shared.artifact_store import clear_artifact_pin, revert_artifact_to_pending

    stale = find_stale_selected_artifacts(today=today)
    reclaimed: list[dict[str, object]] = []
    for finding in stale:
        artifact_id = str(finding["artifact_id"])
        if dry_run:
            reclaimed.append(finding)
            continue
        session = get_cassandra_session()
        if finding.get("has_current_reference"):
            # Stale row only -- the artifact is legitimately SELECTED
            # elsewhere for a current/future day. Never touch its status.
            session.execute(ToComposeStmts.DELETE_SLOT, (finding["compose_day"], finding["slot"]))
            reclaimed.append({**finding, "action": "stale_row_deleted_only"})
            continue
        if revert_artifact_to_pending(artifact_id):
            if finding["lane"] == "human":
                clear_artifact_pin(artifact_id)
            session.execute(ToComposeStmts.DELETE_SLOT, (finding["compose_day"], finding["slot"]))
            reclaimed.append({**finding, "action": "reverted_to_pending"})

    return {"dry_run": dry_run, "reclaimed_count": len(reclaimed), "reclaimed": reclaimed}


def preview_to_compose_for_day(day: str) -> dict[str, object]:
    """Read-only preview of what select_to_compose_for_day(day) currently would pick -- the query behind the admin shadow-selection dashboard.

    NEVER mutates artifact status and NEVER touches `to_compose` -- unlike
    select_to_compose_for_day, this is safe to call on every page load /
    poll. Uses the exact same ranking (_rank_platform_picks over
    list_pending_artifacts()'s own priority-DESC order) so "what would be
    picked" here matches what a real selection run would pick right now.

    Returns every PENDING artifact (not just the selected ones), each with
    a freshly recomputed priority breakdown (word_count/timeliness/
    ecosystem_listed/skip_count -- the same pure functions the daily sweep
    uses, all four of SCORE_COMPONENTS), which lane (if any) this preview
    would put it in, and which pool (new_service vs update -- see
    _artifact_pool) its own service belongs to, independent of whether it
    was actually selected. The recomputed total can drift slightly above/
    below the artifact's stored `priority` between sweeps (the sweep runs
    roughly every 24h; timeliness decays continuously) -- that's
    intentional, showing the score as of right now rather than as of the
    last sweep. Ordering, however, follows list_pending_artifacts()'s
    stored-priority order, matching what a real (unmutated) selection run
    would actually pick, not this preview's own live recompute.

    2026-08-27: when this process can't actually run ecosystem_listed_score's
    real logic (every backend-computed preview -- see
    ecosystem_scoring_available's docstring), the top-level result also
    carries `"ecosystem_scoring_unavailable": True` so a caller (the admin
    UI) can tell every item's `ecosystem_listed: 0.0` apart from a genuinely
    computed zero, rather than silently showing a number that looks measured
    but wasn't. Absent when this process CAN compute it (i.e. always absent
    in workers).
    """
    from app.core import config as cfg

    from algorand_shared.artifact_priority import (
        ecosystem_listed_score,
        ecosystem_scoring_available,
        skip_count_score,
        timeliness_score,
        word_count_score,
    )
    from algorand_shared.artifact_store import get_artifact_content

    # Probed once per preview build (not once per item -- it's a pure
    # capability check, the answer can't differ between artifacts within the
    # same process). See ecosystem_scoring_available's own docstring: always
    # False in backend (workers-only crawler/classifier deps don't exist
    # there), always True in workers.
    ecosystem_available = ecosystem_scoring_available()

    pending = list_pending_artifacts()
    human_pick = next((a for a in pending if a.human_pick_day == day), None)

    platform_n = max(0, cfg.NEWS_MAX_ARTICLES_PER_DAY - 1)
    pool_cache: dict[str, str] = {}
    platform_picks = _rank_platform_picks(
        pending, human_pick=human_pick, platform_n=platform_n, pool_cache=pool_cache
    )
    platform_ids = {a.artifact_id for a in platform_picks}
    pool_counts = {NEW_SERVICE_POOL: 0, UPDATE_POOL: 0}
    for artifact in platform_picks:
        pool_counts[_artifact_pool(artifact, cache=pool_cache)] += 1

    items: list[dict[str, object]] = []
    for artifact in pending:
        content = get_artifact_content(artifact.artifact_id)
        breakdown = {
            "word_count": word_count_score(content.content if content else ""),
            "timeliness": timeliness_score(artifact.event_date, artifact.created_at),
            "ecosystem_listed": ecosystem_listed_score(
                artifact.url, content.content if content else ""
            ),
            "skip_count": skip_count_score(content.metadata if content else None),
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
                "pool": _artifact_pool(artifact, cache=pool_cache),
            }
        )

    result: dict[str, object] = {
        "status": "ok",
        "compose_day": day,
        "human_picked": human_pick is not None,
        "platform_slots_filled": len(platform_picks),
        "platform_slots_available": platform_n,
        "platform_pool_counts": pool_counts,
        "items": items,
    }
    if not ecosystem_available:
        # Present (and only ever True) when this process couldn't actually
        # run ecosystem_listed_score's real logic -- i.e. every backend-
        # computed preview -- so every item's `ecosystem_listed: 0.0` above
        # reads as "not computed here", not "measured and found to be zero".
        # Absent entirely in workers, where the real dependencies exist.
        result["ecosystem_scoring_unavailable"] = True
    return result
