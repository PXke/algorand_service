"""Celery tasks that drain the publish queue (breaking, standard, review) on their beats."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery_app
from app.core import config
from app.modules.ai.mistral_credit_guard import is_credit_exhausted
from app.modules.newspaper.breaking_credibility import (
    BreakingAssessment,
    assess_breaking_credibility,
)
from app.modules.newspaper.publish_policy import (
    PublishKind,
    PublishTier,
    evaluate_breaking_publish,
    evaluate_standard_publish,
    remaining_breaking_publish_slots,
    remaining_standard_publish_slots,
)
from app.modules.newspaper.publish_queue_store import (
    QueuedPublishRow,
    clear_human_pick,
    is_terminal_outcome,
    list_pending_queue,
    mark_queue_done,
    mark_queue_status,
    queue_row_tier,
    record_queue_reason,
)
from app.modules.newspaper.publish_schedule import record_standard_publish
from app.modules.newspaper.tasks.publish_tasks import publish_from_queued_row

logger = logging.getLogger(__name__)


def _row_needs_review(row: QueuedPublishRow) -> bool:
    """Would the classifier hold this row for admin review? Review-bound items bypass publish pacing — they don't hit the feed until approved, so spacing them out only delays training feedback. Reads the signals computed once at ingest; recomputes only for rows queued before signals existed.

    Scam/incident topics always need review regardless of what the ML
    classifier thinks — both a sound editorial policy (never auto-publish
    a serious accusation without a human check) and the fix for a real
    wasted-compute bug: this pre-compute check is what lets a drain skip
    composing when the review queue is already full (see
    _breaking_review_slot_veto). Without the topic short-circuit, a
    legitimately well-written page that got MIS-classified as scam_alert
    (2026-07-10: perawallet.app) would score as "confidently publishable" by
    the ML signals and sail past this check into a full — and wasted —
    research + compose pass, since a scam/incident piece must always route
    to review no matter what the ML classifier's confidence says.
    """
    from app.modules.newspaper.publish_policy import PublishTopic, is_breaking_topic

    try:
        if is_breaking_topic(PublishTopic(row.topic)):
            return True
    except ValueError:
        pass

    from app.modules.ai.content_signals import ContentSignals

    signals = ContentSignals.from_payload(row.payload.get("signals"))
    if signals is not None:
        return signals.needs_review

    from app.modules.ai.content_categorizer import _fallback_category
    from app.modules.ai.publish_classifier import predict_publish

    text = str(row.payload.get("page_text", ""))
    category = _fallback_category(text, row.scrape_url)
    decision, _confidence = predict_publish(text, row.scrape_url, category)
    return decision is not True


def _pending_for_tier(tier: PublishTier, *, limit: int) -> list:
    return [row for row in list_pending_queue(limit=limit) if queue_row_tier(row) == tier]


def _pending_feed_backlog_full() -> bool:
    """True when the backlog (articles.status='backlog') already holds PENDING_FEED_MAX_DEPTH+ approved articles awaiting paced release. Composing further ahead than that only burns budget to publish staler content later — the auto-approve → backlog path bypasses the 1-slot review throttle, so without this check hourly drains composed all night (2026-07-16: six articles / two days of inventory queued overnight). Fails open: a Cassandra blip must not stop the pipeline."""
    from algorand_shared.article_transitions import list_backlog_articles

    from app.core import config as cfg

    try:
        return len(list_backlog_articles()) >= cfg.PENDING_FEED_MAX_DEPTH
    except Exception:
        logger.warning("pending-feed depth check failed — treating as not full", exc_info=True)
        return False


def _domain_capped(row: QueuedPublishRow) -> bool:
    """True when this web source already created its allotted articles today (COMPOSE_MAX_PER_DOMAIN_PER_DAY). Such rows are deferred rather than composed."""
    from app.modules.crawler.domain_tracker import domain_compose_cap_reached
    from app.modules.newspaper.tasks.publish_tasks import _compose_domain_for_row

    dom = _compose_domain_for_row(row)
    return bool(dom and domain_compose_cap_reached(dom))


def _is_editorial_assignment(row: QueuedPublishRow) -> bool:
    """True for an editorial-brief row -- a deliberate, one-off editorial/admin decision (assign_editorial_brief/refresh_editorial_brief already fire the drain immediately on enqueue, by design), not automatic discovery. The diversity cooldowns exist to space out routine coverage of the SAME source recurring on its own; they were never meant to silently defer an explicit human "compose this now" request. Same reasoning the breaking tier already applies (see _BREAKING_VETOES: "breaking is the one tier where a genuine alert must never wait behind a cooldown from routine coverage of the same source" -- an editorial assignment is the standard tier's equivalent case."""
    return (row.payload or {}).get("source_kind") == "editorial_assignment"


def _domain_in_cooldown(row: QueuedPublishRow) -> bool:
    """True when this web source published/composed within the diversity cooldown (COMPOSE_DOMAIN_COOLDOWN_HOURS). Unlike the daily cap, cooldown is short-lived, so such rows are left pending (not deferred) to retry once it clears."""
    if _is_editorial_assignment(row):
        return False
    from app.modules.crawler.domain_tracker import domain_in_cooldown
    from app.modules.newspaper.tasks.publish_tasks import _compose_domain_for_row

    dom = _compose_domain_for_row(row)
    return bool(dom and domain_in_cooldown(dom))


def _service_in_cooldown(row: QueuedPublishRow) -> bool:
    """True when this SERVICE (across all its domains) published/composed within its diversity cooldown. Complements _domain_in_cooldown for a project whose domains don't share a registrable domain, so the per-domain check alone can't see the repeat (e.g. a project's own site + a separate Medium blog).

    Root-caused 2026-08-04: an admin's explicit brief refresh (a deliberate
    "recompose this now" action) was silently vetoed here and skipped to the
    next-priority row instead, hours after the brief's own prior compose --
    the cooldown is meant for routine automatic coverage recurring on its
    own, not an explicit editorial trigger. See _is_editorial_assignment.
    """
    if _is_editorial_assignment(row):
        return False
    from app.modules.crawler.domain_tracker import service_in_cooldown

    return bool(row.service_id and service_in_cooldown(row.service_id))


def _novelty_collapsed(row: QueuedPublishRow) -> bool:
    """Fresh novelty at drain time. The enqueue-time novelty is a snapshot; a story on the same subject may have PUBLISHED after this row entered the queue (the Defly case: a newsletter about a wallet we had just covered), so the stored priority silently overstates it. Recompute against articles published NOW and cut the row when novelty has collapsed — one cheap Typesense query beats a six-minute duplicate compose. Fails open (0.0 similarity → not collapsed) when Typesense is unavailable.

    Uses the SAME boundaries as the compose-time duplicate check
    (publish_tasks.py's _novelty_duplicate_veto) rather than the old, more
    lenient NOVELTY_DUPLICATE_FLOOR — a row in between the two used to survive
    this drain-time check only to be discarded as a duplicate mid-compose,
    wasting a full Mistral call. title_sim and content_sim are both
    token-Jaccard (see article_grader.recent_content_similarity), so they're
    on the same scale and combined via max() under one boundary.
    """
    from app.modules.newspaper.article_grader import (
        recent_content_similarity,
        recent_title_similarity,
    )

    title = str(row.payload.get("page_title", ""))
    text = str(row.payload.get("page_text", ""))
    title_sim, _ = recent_title_similarity(title)
    content_sim, _ = recent_content_similarity(title, text)
    return max(title_sim, content_sim) >= config.NOVELTY_MAX_SIMILARITY


def _brief_archived(row: QueuedPublishRow) -> bool:
    """True when this row is an editorial-brief assignment whose brief is no longer active (archived/deactivated since it was enqueued). Archiving a brief does NOT purge its already-queued assignment, so without this a retired brief still composes once when the drain reaches its stale row — 2026-07-20: an archived duplicate wallet brief drained and AUTO-PUBLISHED a wrong article. Fails open (compose) on any lookup error — a transient blip must not silently drop legitimate assignments."""
    payload = row.payload or {}
    if payload.get("source_kind") != "editorial_assignment":
        return False
    brief_id = str(payload.get("brief_id", "")).strip()
    if not brief_id:
        return False
    try:
        from app.modules.newspaper.editorial_assignment import get_brief

        brief = get_brief(brief_id)
    except Exception:
        return False
    return brief is not None and brief.status != "active"


@dataclass(frozen=True)
class _DrainGate:
    """One pre-compose veto in the standard drain: ``check(row)`` True means the row is skipped this run with ``name`` as its reported status. ``mark_status`` moves the row out of the pending lane (``deferred``/``expired``); None leaves it pending to retry next beat (right for short-lived cooldowns)."""

    name: str
    check: Callable[..., bool]
    mark_status: str | None = None


# Evaluated in order, first match wins. The order is load-bearing:
# cooldown checks MUST run before the review branch in the drain loop (a
# review draft still re-covers the same project), and novelty runs last so a
# capped/cooling row doesn't spend a Typesense query.
_PRE_COMPOSE_GATES: tuple[_DrainGate, ...] = (
    # First: an assignment for a brief that's since been archived must never
    # compose — cheap, decisive, and drops the stale row out of pending.
    _DrainGate("brief_archived", _brief_archived, mark_status="expired"),
    _DrainGate("domain_capped", _domain_capped, mark_status="deferred"),
    _DrainGate("domain_cooldown", _domain_in_cooldown),
    _DrainGate("service_cooldown", _service_in_cooldown),
    _DrainGate("novelty_collapsed", _novelty_collapsed, mark_status="expired"),
)


def _run_pre_compose_gates(row: QueuedPublishRow) -> _DrainGate | None:
    """First gate that vetoes this row, or None when all pass.

    Checks are resolved through the module namespace at call time (not the
    reference frozen into the tuple at import) so monkeypatching e.g.
    ``queue_drain_tasks._domain_in_cooldown`` keeps working — that seam is how
    the existing drain tests fake cooldown/cap state.
    """
    for gate in _PRE_COMPOSE_GATES:
        check = globals().get(gate.check.__name__, gate.check)
        if check(row):
            return gate
    return None


def _resolve(row: QueuedPublishRow, outcome: dict) -> str:
    """Mark the queue row done when its compose outcome resolved it (published / review / duplicate); leave it pending otherwise. Returns the status string. Single source of truth for which outcomes dequeue a row — a status missing from TERMINAL_OUTCOMES is the bug class behind 'the same topic reappears'. An outcome may also carry ``queue_status`` (e.g. the content-quality veto's "expired") to retire the row under that status without counting as a successful resolution. Either way the outcome is persisted as the row's last_reason, so the admin queue view can answer "why is/was this row here"."""
    status = str(outcome.get("status", ""))
    reason = str(outcome.get("reason", "") or status)
    queue_status = str(outcome.get("queue_status", ""))
    if is_terminal_outcome(outcome):
        mark_queue_done(row.queue_id, reason=reason)
    elif queue_status:
        mark_queue_status(row.queue_id, queue_status, reason=reason)
    elif status:
        record_queue_reason(row.queue_id, reason)
    return status


def _compose_review_row(row: QueuedPublishRow) -> dict:
    """Compose one review-bound row at standard tier and resolve it. Shared by the standard drain's review branch and ensure_review_ready."""
    outcome = publish_from_queued_row(row, publish_tier=PublishTier.STANDARD)
    _resolve(row, outcome)
    return outcome


@dataclass
class _BreakingVetoCtx:
    """Loop state the breaking vetoes need beyond the row: whether the single review slot is occupied this run, and the credibility assessment — set by the credibility veto (last gate) and reused by the drain to tag the SUCCESS outcome with its method, so it's only computed once and only for rows that reach that gate. Mutable on purpose (unlike _DrainGate rows)."""

    row: object
    review_full: bool
    assessment: BreakingAssessment | None = None


def _breaking_policy_veto(ctx: _BreakingVetoCtx) -> dict | None:
    """Schedule/kind/diff policy (daily cap, weekly-not-queued, small diff)."""
    decision = evaluate_breaking_publish(
        PublishKind(ctx.row.publish_kind),
        diff=ctx.row.payload.get("diff"),
        source_kind=ctx.row.payload.get("source_kind"),
    )
    if not decision.allowed:
        return {"status": "skipped", "reason": decision.reason}
    return None


def _breaking_review_slot_veto(ctx: _BreakingVetoCtx) -> dict | None:
    """Composing a review-bound item into a full review queue just returns "review_queue_full" — a status the drain does NOT mark done, so the row would recompose every beat, burning a full Mistral loop each time. Leave it pending until the admin clears the review queue."""
    if ctx.review_full and _row_needs_review(ctx.row):
        return {"status": "skipped", "reason": "review_queue_full"}
    return None


def _breaking_credibility_veto(ctx: _BreakingVetoCtx) -> dict | None:
    """Breaking must be corroborated (alert keywords + evidence).

    The assessment is a pure heuristic over the row's static page_text, so a
    not-credible verdict can never change on a later beat — retire the row
    (queue_status) instead of leaving it pending. A pending row here was
    re-assessed every ~2-minute breaking beat forever AND held the service's
    one-pending-row slot hostage: observed 2026-07-17, a hay-app row stuck
    "not_credible" for 7 days, starving all hay-app coverage. The next scrape
    re-offers the story fresh if it grows real evidence.
    """
    ctx.assessment = assess_breaking_credibility(
        page_text=str(ctx.row.payload.get("page_text", "")),
        source_url=ctx.row.scrape_url,
        topic=ctx.row.topic,
    )
    if not ctx.assessment.credible:
        return {
            "status": "skipped",
            "reason": f"not_credible:{ctx.assessment.reason}",
            "method": ctx.assessment.method,
            "queue_status": "expired",
        }
    return None


# Evaluated in order, first non-None outcome wins — same pattern as the
# standard drain's _PRE_COMPOSE_GATES and compose-side _PRE_COMPOSE_VETOES.
# Deliberately NO domain/service cooldown or novelty veto here (owner
# decision, re-confirmed 2026-07-17): breaking is the one tier where a
# genuine alert must never wait behind a cooldown from routine coverage
# of the same source. Credibility + policy caps are the safety net.
_BREAKING_VETOES = (
    _breaking_policy_veto,
    _breaking_review_slot_veto,
    _breaking_credibility_veto,
)


def _run_breaking_vetoes(ctx: _BreakingVetoCtx) -> dict | None:
    """First veto outcome for this breaking row, or None when all pass."""
    for veto in _BREAKING_VETOES:
        outcome = veto(ctx)
        if outcome is not None:
            return outcome
    return None


def _record_breaking_veto_outcome(row: QueuedPublishRow, veto_outcome: dict) -> dict:
    """Persist a breaking-drain veto's disposition on the row and return its results-list entry.

    A veto may carry queue_status (credibility: a permanent verdict on
    static text) to retire the row; the transient vetoes (daily cap, review
    slot) leave it pending for a later beat.
    """
    veto_queue_status = str(veto_outcome.get("queue_status", ""))
    veto_reason = str(veto_outcome.get("reason", "skipped"))
    if veto_queue_status:
        mark_queue_status(row.queue_id, veto_queue_status, reason=veto_reason)
    else:
        record_queue_reason(row.queue_id, veto_reason)
    return {"queue_id": row.queue_id, **veto_outcome}


def _publish_breaking_row(
    row: QueuedPublishRow, ctx: _BreakingVetoCtx, review_full: bool
) -> tuple[dict, str, bool]:
    """Compose+publish one vetted breaking row. Returns (results_entry, status, updated_review_full).

    Breaking news (scams/incidents) is urgent and rare — exempt from the
    per-website daily article cap so a critical alert is never held.
    """
    from app.modules.crawler.classifier_review_store import review_queue_full

    outcome = publish_from_queued_row(
        row, publish_tier=PublishTier.BREAKING, enforce_domain_cap=False
    )
    status = _resolve(row, outcome)
    if status in ("review", "duplicate", "duplicate_review_pending"):
        # Filling the review slot closes it for the rest of this run.
        review_full = review_queue_full()
    # ctx.assessment is guaranteed set: the credibility veto (last gate) ran
    # and passed for any row that reaches a compose.
    entry = {"queue_id": row.queue_id, **outcome, "credibility": ctx.assessment.method}
    return entry, status, review_full


@celery_app.task(
    name="app.tasks.newspaper.drain_breaking_publish_queue",
    soft_time_limit=config.COMPOSE_TASK_SOFT_TIME_LIMIT,
    time_limit=config.COMPOSE_TASK_TIME_LIMIT,
)
def drain_breaking_publish_queue() -> dict[str, object]:
    """Publish breaking-tier items immediately up to the separate daily cap."""
    if config.AUTO_COMPOSE_PAUSED:
        return {"status": "skipped", "reason": "auto_compose_paused", "published": 0}
    if is_credit_exhausted(config.LLM_PROVIDER_WRITER):
        return {"status": "skipped", "reason": "mistral_credit_exhausted", "published": 0}
    slots = remaining_breaking_publish_slots()
    if slots <= 0:
        return {"status": "skipped", "reason": "breaking_daily_cap_reached", "published": 0}

    pending = _pending_for_tier(PublishTier.BREAKING, limit=config.PUBLISH_QUEUE_BATCH_LIMIT)
    published = 0
    results: list[dict[str, str]] = []

    from app.modules.crawler.classifier_review_store import review_queue_full

    # Same guard as the standard drain: when the single review slot is already
    # occupied, composing a review-bound breaking item just returns
    # "review_queue_full" — a status this drain does NOT mark done, so the row
    # would recompose every beat, burning a full Mistral loop each time. Leave
    # it pending until the admin clears the review queue.
    review_full = review_queue_full()
    try:
        for row in pending:
            if published >= slots:
                break
            # Per-row vetoes (_BREAKING_VETOES): publish policy, review-slot
            # availability, credibility — in that order.
            ctx = _BreakingVetoCtx(row=row, review_full=review_full)
            veto_outcome = _run_breaking_vetoes(ctx)
            if veto_outcome is not None:
                results.append(_record_breaking_veto_outcome(row, veto_outcome))
                continue

            entry, status, review_full = _publish_breaking_row(row, ctx, review_full)
            if status == "published":
                published += 1
            elif status == "rate_limited":
                return {
                    "status": "skipped",
                    "reason": entry.get("reason", "rate_limited"),
                    "published": published,
                    "results": results,
                }
            results.append(entry)
    except SoftTimeLimitExceeded:
        # Killed mid-compose: the in-flight row was never marked done, so it
        # stays pending. Return partial progress instead of crashing.
        return {
            "status": "interrupted",
            "tier": "breaking",
            "reason": "soft_time_limit",
            "published": published,
            "results": results,
        }

    return {
        "status": "ok",
        "tier": "breaking",
        "published": published,
        "slots_remaining_start": slots,
        "results": results,
    }


def _release_due_backlog(slots: int) -> dict | None:
    """Release an admin-approved backlog item first (held because the cap was full when it was approved) — cheap, no compose cost, and shares this exact pacing gate/budget with composing something new below (folded in from the old standalone drain_approved_feed_queue task/beat entry). Only gates THIS step on the interval, same as before — review composition below intentionally bypasses publish pacing (it doesn't hit the feed until approved), so a blanket check up here would wrongly delay it too. Returns a terminal drain result when something was released, else None to fall through to fresh composition."""
    from app.modules.newspaper.publish_schedule import is_standard_publish_due

    due, _detail = is_standard_publish_due()
    if not due:
        return None
    backlog = _release_pending_feed_backlog(slots=slots)
    if backlog.get("published", 0):
        return {**backlog, "tier": "standard", "source": "pending_feed_backlog"}
    return None


def _record_pre_compose_gate(row: QueuedPublishRow, fired: _DrainGate) -> dict:
    """Persist a fired pre-compose gate's disposition on the row and return its results-list entry."""
    if fired.mark_status:
        mark_queue_status(row.queue_id, fired.mark_status, reason=fired.name)
    else:
        record_queue_reason(row.queue_id, fired.name)
    return {"queue_id": row.queue_id, "status": fired.name}


def _process_review_row(
    row: QueuedPublishRow, *, review_full: bool, backlog_full: bool, reviews_composed: int
) -> tuple[dict | None, bool, int, int]:
    """Compose a review-bound row (or skip it for this run). Returns (results_entry_or_None, updated_review_full, updated_reviews_composed, published_delta)."""
    if review_full or reviews_composed >= config.REVIEW_COMPOSE_BATCH_LIMIT:
        return None, review_full, reviews_composed, 0
    if backlog_full:
        # A full day of releases is already queued — composing more now is
        # pure cost. Rows stay pending; composing resumes once the paced
        # release drains the backlog below the cap.
        return None, review_full, reviews_composed, 0

    from app.modules.crawler.classifier_review_store import review_queue_full

    outcome = _compose_review_row(row)
    outcome_status = outcome.get("status")
    published_delta = 0
    if outcome_status == "review":
        reviews_composed += 1
        # The slot we just filled may now be full (MAX_PENDING_REVIEWS is
        # typically 1) — re-check so we don't compose more reviews this run
        # only to discard them with "review_queue_full".
        review_full = review_queue_full()
    elif outcome_status == "published":
        # Fresh-auto-approve published straight to the feed: that is a real
        # standard-tier release. Advance the pacing clock and spend this
        # run's feed budget — on 2026-07-15 neither happened and one drain
        # run chain-published three articles minutes apart.
        record_standard_publish()
        published_delta = 1
    elif outcome_status == "approved_backlog":
        # A full compose was spent even though nothing hit the feed — count
        # it toward the per-run compose budget.
        reviews_composed += 1
    entry = {"queue_id": row.queue_id, **outcome}
    return entry, review_full, reviews_composed, published_delta


def _publish_standard_row(
    row: QueuedPublishRow, published: int
) -> tuple[dict | None, int, dict | None]:
    """Evaluate policy and compose+publish one non-review standard row. Returns (results_entry, published_delta, early_stop_result)."""
    kind = PublishKind(row.publish_kind)
    diff = row.payload.get("diff")
    decision = evaluate_standard_publish(
        kind, diff=diff, source_kind=row.payload.get("source_kind")
    )
    if not decision.allowed:
        return None, 0, {"status": "skipped", "reason": decision.reason, "published": 0}

    outcome = publish_from_queued_row(row, publish_tier=PublishTier.STANDARD)
    status = _resolve(row, outcome)
    if status == "published":
        record_standard_publish()
        return {"queue_id": row.queue_id, **outcome}, 1, None
    if status == "rate_limited":
        return (
            None,
            0,
            {
                "status": "skipped",
                "reason": outcome.get("reason", "rate_limited"),
                "published": published,
            },
        )
    return {"queue_id": row.queue_id, **outcome}, 0, None


def _standard_drain_setup() -> tuple[int, dict | None]:
    """Slot budget and early-exit checks before composing anything: auto-compose pause, daily cap, admin-approved backlog release, and credit exhaustion. Returns (slots, early_result) — early_result is a terminal drain result the caller should return immediately, else None to proceed.

    Backlog release never composes (it only publishes already-paid-for
    work), so it must run even while Mistral credit is exhausted -- but NOT
    while auto-compose is explicitly paused, since that's an operator saying
    "nothing automatic happens right now," full stop.
    """
    if config.AUTO_COMPOSE_PAUSED:
        return 0, {"status": "skipped", "reason": "auto_compose_paused", "published": 0}
    slots = remaining_standard_publish_slots()
    if slots <= 0:
        return slots, {"status": "skipped", "reason": "standard_daily_cap_reached", "published": 0}

    backlog_result = _release_due_backlog(slots)
    if backlog_result is not None:
        return slots, backlog_result

    if is_credit_exhausted(config.LLM_PROVIDER_WRITER):
        return slots, {"status": "skipped", "reason": "mistral_credit_exhausted", "published": 0}

    return slots, None


def _today_str() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


def _select_lane_for_today(pending: list[QueuedPublishRow]) -> str | None:
    """Which of the 3 daily lanes this run's fresh compose should draw from.

    Lanes are human pick / biggest-significant / genuinely-new. Returns None
    to fall back to the plain priority order (all 3 lanes already used
    today, or a lane's pool was empty when its turn came — a quiet day must
    never stall a slot).

    Order matters: a human pick always wins if present and unused; discovery
    (Lane 3, "genuinely new") is checked before scale (Lane 2, "biggest") so
    a day with both a discovery and an update candidate doesn't let the
    (usually more numerous) update pool crowd out the one-shot discovery
    lane.
    """
    from app.modules.newspaper.publish_daily_guard import lanes_used_today

    today = _today_str()
    used = lanes_used_today()
    if "human" not in used and any(
        getattr(row, "human_pick_day", None) == today for row in pending
    ):
        return "human"
    if "discovery" not in used and any(
        row.publish_kind == PublishKind.SERVICE_DISCOVERY.value for row in pending
    ):
        return "discovery"
    if "scale" not in used and any(
        row.publish_kind != PublishKind.SERVICE_DISCOVERY.value for row in pending
    ):
        return "scale"
    return None


def _record_lane_consumed(row: QueuedPublishRow, lane: str | None) -> None:
    """After a successful fresh compose+publish, mark the lane spent and clear a consumed human pin."""
    if lane is None:
        return
    from app.modules.newspaper.publish_daily_guard import record_lane_used

    record_lane_used(lane)
    if lane == "human":
        clear_human_pick(row.queue_id)


def _publish_standard_row_for_lane(
    row: QueuedPublishRow, published: int, lane: str | None
) -> tuple[dict | None, int, dict | None]:
    """`_publish_standard_row`, gated by this run's selected lane (see `_select_lane_for_today`).

    A non-matching row is left pending (never marked/skipped as rejected) so
    a later row or a later run can take it. Returns the same
    (entry, published_delta, early_stop) shape as `_publish_standard_row`.
    """
    if lane is not None and not _row_matches_lane(row, lane):
        return None, 0, None
    entry, published_delta, early_stop = _publish_standard_row(row, published)
    if published_delta:
        _record_lane_consumed(row, lane)
    return entry, published_delta, early_stop


def _row_matches_lane(row: QueuedPublishRow, lane: str) -> bool:
    if lane == "human":
        return getattr(row, "human_pick_day", None) == _today_str()
    if lane == "discovery":
        return row.publish_kind == PublishKind.SERVICE_DISCOVERY.value
    if lane == "scale":
        return row.publish_kind != PublishKind.SERVICE_DISCOVERY.value
    return True


@celery_app.task(
    name="app.tasks.newspaper.drain_standard_publish_queue",
    soft_time_limit=config.COMPOSE_TASK_SOFT_TIME_LIMIT,
    time_limit=config.COMPOSE_TASK_TIME_LIMIT,
)
def drain_standard_publish_queue() -> dict[str, object]:
    """Publish standard-tier items on the ~3h schedule, up to 7/day.

    Composes at most one fresh row per run, so the 3-lane split (human pick /
    biggest-significant / genuinely-new) plays out ACROSS runs over the day:
    each run's `_select_lane_for_today` call picks whichever lane hasn't had
    its slot filled yet, and only rows matching that lane are eligible for
    THIS run's fresh compose below (review-bound rows are unaffected — see
    _process_review_row, which is intentionally outside the lane split).
    """
    slots, early_result = _standard_drain_setup()
    if early_result is not None:
        return early_result

    pending = _pending_for_tier(PublishTier.STANDARD, limit=config.PUBLISH_QUEUE_BATCH_LIMIT)
    lane = _select_lane_for_today(pending)
    published = 0
    results: list[dict[str, str]] = []

    from app.modules.crawler.classifier_review_store import review_queue_full

    # If the review slot is already full (admin hasn't acted on the pending item),
    # composing more review-bound articles just burns full Mistral agentic loops on
    # a result that gets discarded with "review_queue_full". Skip them until the
    # admin clears the queue; publish-worthy items still flow below.
    review_full = review_queue_full()
    backlog_full = _pending_feed_backlog_full()
    reviews_composed = 0
    try:
        for row in pending:
            # Pre-compose vetoes (_PRE_COMPOSE_GATES): per-website daily cap,
            # domain/service diversity cooldowns, and the drain-time duplicate
            # cut, in that order. All run BEFORE the review branch below (a
            # review draft still re-covers the same project/story).
            fired = _run_pre_compose_gates(row)
            if fired is not None:
                results.append(_record_pre_compose_gate(row, fired))
                continue

            if _row_needs_review(row):
                entry, review_full, reviews_composed, published_delta = _process_review_row(
                    row,
                    review_full=review_full,
                    backlog_full=backlog_full,
                    reviews_composed=reviews_composed,
                )
                published += published_delta
                if entry is not None:
                    results.append(entry)
                continue

            if published >= 1:
                break
            entry, published_delta, early_stop = _publish_standard_row_for_lane(
                row, published, lane
            )
            if early_stop is not None:
                return {**early_stop, "results": results}
            published += published_delta
            if entry is not None:
                results.append(entry)
    except SoftTimeLimitExceeded:
        # Killed mid-compose despite the budget guard: return partial progress.
        # The in-flight row was never marked done, so it stays pending.
        return {
            "status": "interrupted",
            "tier": "standard",
            "reason": "soft_time_limit",
            "published": published,
            "results": results,
        }

    return {
        "status": "ok",
        "tier": "standard",
        "published": published,
        "slots_remaining_start": slots,
        "results": results,
    }


@celery_app.task(name="app.tasks.newspaper.reap_stale_compose_sessions")
def reap_stale_compose_sessions() -> dict[str, int]:
    """Maintenance beat: mark any compose_sessions row stuck researching/writing past the staleness window as "stale" (see tool_insights_store for why one can get orphaned there)."""
    from app.modules.ai.tool_insights_store import reap_stale_compose_sessions as _reap

    return _reap()


@celery_app.task(name="app.tasks.newspaper.reap_stale_translation_sessions")
def reap_stale_translation_sessions() -> dict[str, int]:
    """Maintenance beat: mark any translation_sessions row stuck 'running' past the staleness window as "stale" (see translation_session_store for why one can get orphaned there -- a hung or killed language mid-translate)."""
    from app.modules.ai.translation_session_store import (
        reap_stale_translation_sessions as _reap,
    )

    return _reap()


def _curated_discovery(scrape_url: str) -> bool:
    """True when the row's domain came from a curated listing (ecosystem directory or case-study sync) — best-effort, False on any failure."""
    try:
        from app.modules.crawler.domain_tracker import domain_from_url
        from app.modules.crawler.ecosystem_sync import ecosystem_listed_domains

        return domain_from_url(scrape_url) in ecosystem_listed_domains()
    except Exception:
        return False


@celery_app.task(name="app.tasks.newspaper.expire_stale_queue_items")
def expire_stale_queue_items() -> dict[str, object]:
    """Queue maintenance (Phase 5).

    - Expire stale `announce`-phase rows that were never published before the event passed.
    - Defer low-score rows that sat in the queue too long; index their page text
      for search first when available (`indexed_only`), otherwise mark `deferred`.
    """
    import time

    from app.modules.search.tasks.index_tasks import index_crawled_page

    now_epoch = int(time.time())
    announce_max_age = config.PUBLISH_ANNOUNCE_EXPIRE_HOURS * 3600
    defer_after = config.PUBLISH_DEFER_AFTER_HOURS * 3600

    expired = 0
    deferred = 0
    indexed_only = 0

    for row in list_pending_queue(limit=config.PUBLISH_QUEUE_BATCH_LIMIT * 4):
        age = now_epoch - row.created_at_epoch
        event_phase = str(row.payload.get("event_phase", ""))

        if event_phase == "announce" and age > announce_max_age:
            mark_queue_status(row.queue_id, "expired", reason="announce_event_passed")
            expired += 1
            continue

        if row.priority < config.PUBLISH_DEFER_PRIORITY_THRESHOLD and age > defer_after:
            if row.publish_kind == "service_discovery" and _curated_discovery(row.scrape_url):
                # Curated once-ever introductions (ecosystem directory /
                # Foundation case-study subjects) are chain-silent by nature,
                # so their keyword-driven priority is structurally low (~27 =
                # 0.45 anchor x discovery weight, under the 45 threshold).
                # They're latency-tolerant by design: leave them pending until
                # a quiet drain slot picks them up instead of parking them.
                continue
            page_text = str(row.payload.get("page_text", ""))
            if page_text:
                index_crawled_page.delay(
                    url=row.scrape_url,
                    title=str(row.payload.get("page_title", "")),
                    text=page_text,
                    service_id=row.service_id,
                )
                mark_queue_status(row.queue_id, "indexed_only", reason="stale_low_priority")
                indexed_only += 1
            else:
                mark_queue_status(row.queue_id, "deferred", reason="stale_low_priority")
                deferred += 1

    return {
        "status": "ok",
        "expired": expired,
        "deferred": deferred,
        "indexed_only": indexed_only,
    }


def _release_pending_feed_backlog(*, slots: int) -> dict[str, object]:
    """Release ONE admin-approved article that was held because the daily feed cap was already reached (pending_feed_queue), interest order.

    Shares the SAME pacing clock as the caller (is_standard_publish_due /
    NEWS_STANDARD_INTERVAL_HOURS), not a separate one — a held article going
    out via this path is still a standard-tier release and must respect the
    same cadence. Previously ran as its own Celery task/beat entry
    (drain_approved_feed_queue) with its own feed_release_due/
    APPROVED_FEED_MIN_GAP_SECONDS (1h default), which let backlog releases
    come out far more often than the intended 8h-apart rhythm (root-caused
    2026-07-14 via the AlgoVanity article) — folded into
    drain_standard_publish_queue since both already share one pacing gate
    and one daily budget, so a separate task+beat entry was an avoidable
    extra moving part that most cycles did nothing anyway.
    """
    from datetime import UTC, datetime

    from algorand_shared.article_statements import ArticlesStmts
    from algorand_shared.article_transitions import list_backlog_articles

    from app.core import config as cfg
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import PendingFeedStmts

    session = get_cassandra_session()
    bucket = getattr(cfg, "NEWS_FEED_BUCKET", "main") or "main"
    # One per run — the standard-publish interval pacing keeps releases at
    # NEWS_STANDARD_INTERVAL_HOURS apart regardless of which path releases them.
    # Ordering now comes from articles.status='backlog' (article-table
    # consolidation Phase 4); pending_feed_queue is still consulted below,
    # per released article, purely to clean up its now-stale old-table row.
    backlog = list_backlog_articles()
    rows = backlog[:1]
    published = 0
    for r in rows:
        # 2026-08-25 (Phase 5): reads `articles` directly (was
        # articles_by_id's GET_FOR_FEED) -- summary/tags/image_url/
        # source_url/published_at all live on the same consolidated row now.
        art = session.execute(ArticlesStmts.GET_FULL_BY_ID, (r.article_id,)).one()
        if art is None:
            # The queue row still gets deleted below (a permanently missing
            # article would otherwise jam this one-row-per-run queue
            # forever), but that silently discards whatever compose spend
            # produced it — log it so the loss is at least visible.
            logger.warning(
                "pending_feed_queue row %s has no matching article — dropping without release",
                r.article_id,
            )
        if art is not None:
            # Time-capsule fix (2026-07-18): the article was composed days
            # ago and stored unlisted — gates added SINCE its compose never
            # saw it. Re-run the body-only self-healing gates now, before it
            # becomes publicly visible; fail-open (an already-approved
            # article is never blocked, only corrected).
            from app.modules.newspaper.release_gates import apply_release_gates

            apply_release_gates(str(r.article_id))
            # A backlog release is a standard-tier publish against the same
            # daily cap as direct publishes — reserve its slot in the SAME
            # atomic counter (2026-07-18 redundancy pruning: releases used
            # to insert feed rows without reserving, silently undercounting
            # the guard's Redis counter for the rest of the day).
            from app.modules.newspaper.publish_daily_guard import (
                release_publish_slot,
                reserve_publish_slot,
            )
            from app.modules.newspaper.publish_policy import PublishTier

            reserved, reserve_reason = reserve_publish_slot(tier=PublishTier.STANDARD)
            if not reserved:
                logger.info("backlog release blocked: %s", reserve_reason)
                break
            # Normally this is the article's FIRST entry into the public feed —
            # art.published_at was stamped at compose time, not release time,
            # so it must be re-stamped now on the `articles` row itself.
            released_at = datetime.now(tz=UTC)
            try:
                # `articles` table backlog -> published status transition
                # (article-table consolidation Phase 5: this is now the sole
                # write, articles_feed/articles_by_id's old dual-write halves
                # were dropped once every read moved onto `articles`).
                from app.modules.newspaper.article_store import transition_article_status

                try:
                    transition_article_status(
                        art.article_id, new_status="published", new_published_at=released_at
                    )
                except Exception:
                    logger.warning(
                        "articles dual-write transition failed for %s", art.article_id,
                        exc_info=True,
                    )
                # Permanent URL slug, claimed at release rather than at compose:
                # a held draft that never ships must not hold the clean slug.
                # Self-contained and non-raising, so it cannot fail the publish.
                from app.modules.newspaper.article_store import _claim_slug_for_feed

                _claim_slug_for_feed(art.article_id, art.title, released_at, status="published")
            except Exception:
                # The article never became visible — hand the slot back so
                # the day's budget isn't burned by a failed write.
                release_publish_slot(tier=PublishTier.STANDARD)
                raise
            published += 1
            record_standard_publish()
            from app.modules.newspaper.tasks.publish_tasks import (
                enqueue_article_translations,
            )

            enqueue_article_translations(str(art.article_id))
            # The article just became publicly visible — same IndexNow ping the
            # direct-publish path sends. Best-effort, never blocks the release.
            try:
                from app.modules.newspaper.article_store import ensure_article_slug
                from app.modules.newspaper.indexnow import ping_article

                # ensure_article_slug is idempotent -- the slug was already
                # claimed by _claim_slug_for_feed above, this just reads it
                # back so IndexNow submits the permanent URL, not the id.
                ping_article(
                    str(art.article_id),
                    slug=ensure_article_slug(art.article_id, art.title),
                )
            except Exception:
                pass
            try:
                from app.modules.newspaper.tasks.distribution_tasks import distribute_article

                distribute_article.delay(article_id=str(art.article_id))
            except Exception:
                logger.warning(
                    "failed to queue distribution for article %s", art.article_id, exc_info=True
                )
        # Old-table cleanup: articles.status has already moved off 'backlog'
        # above (transition_article_status's own delete-old-partition step),
        # this just keeps pending_feed_queue in sync until Phase 5 drops it.
        for old_row in session.execute(PendingFeedStmts.LIST_ALL, (bucket,)):
            if old_row.article_id == r.article_id:
                session.execute(
                    PendingFeedStmts.DELETE,
                    (old_row.bucket, old_row.interest_score, old_row.approved_at, r.article_id),
                )
                break
    return {"status": "ok", "published": published, "slots": slots}


@celery_app.task(name="app.tasks.newspaper.drain_approved_feed_queue")
def drain_approved_feed_queue() -> dict[str, object]:
    """Thin, directly-invocable wrapper around _release_pending_feed_backlog — no longer on its own beat schedule (folded into drain_standard_publish_queue, which now checks the backlog first), kept registered for manual/debug triggers."""
    from app.modules.newspaper.publish_policy import remaining_standard_publish_slots
    from app.modules.newspaper.publish_schedule import is_standard_publish_due

    slots = remaining_standard_publish_slots()
    if slots <= 0:
        return {"status": "skipped", "reason": "daily_cap_reached", "published": 0}

    due, detail = is_standard_publish_due()
    if not due:
        return {"status": "skipped", "reason": detail, "published": 0}

    return _release_pending_feed_backlog(slots=slots)


@celery_app.task(name="app.tasks.newspaper.ensure_review_ready")
def ensure_review_ready() -> dict[str, object]:
    """Keep exactly one composed article waiting in the review queue at all times (when candidates exist), so the admin always has one to act on."""
    if config.AUTO_COMPOSE_PAUSED:
        return {"status": "skipped", "reason": "auto_compose_paused"}
    if is_credit_exhausted(config.LLM_PROVIDER_WRITER):
        return {"status": "skipped", "reason": "mistral_credit_exhausted"}
    from app.modules.crawler.classifier_review_store import review_queue_full

    if review_queue_full():
        return {"status": "skipped", "reason": "review_queue_full"}
    if _pending_feed_backlog_full():
        # Auto-approve routes most composes past the review slot into the
        # backlog — when a full day of releases is already queued, this beat
        # composing "one for the admin" really just kept stacking inventory
        # (2026-07-16 overnight loop).
        return {"status": "skipped", "reason": "pending_feed_backlog_full"}
    pending = _pending_for_tier(PublishTier.STANDARD, limit=8)
    for row in pending:
        if not _row_needs_review(row):
            continue
        # Same diversity guards as the standard drain: never pull a candidate whose
        # registrable domain is over its daily cap or inside its multi-day cooldown.
        # Without this, the review slot surfaces a just-covered (duplicate) domain
        # even though drain_standard_publish_queue would have skipped it.
        if _domain_capped(row) or _domain_in_cooldown(row) or _service_in_cooldown(row):
            continue
        outcome = _compose_review_row(row)
        if outcome.get("status") == "review":
            return {"status": "composed", "queue_id": row.queue_id}
    return {"status": "no_candidate"}
