"""Celery tasks that drain the editorial-room `to_compose` selection into real composes (2026-08-25) -- the live successor to the old publish_queue-based standard/breaking drain.

The BREAKING fast path (separate daily cap, no-cooldown-no-novelty vetoes,
heuristic credibility check, "Breaking:" title prefix) was removed entirely
(owner's call, "it is a concept that didn't work well") rather than folded
into this new system as a priority class -- see PublishTier's docstring and
the deleted breaking_credibility.py.

publish_queue/publish_queue_pending/publish_queue_dedupe (and the one-deploy-
cycle dual-write that fed them from ingest_signal.py/editorial_assignment.py)
were dropped a day later, once this artifact-native path proved stable in
prod -- `drain_to_compose` below reads exclusively from the editorial-room
`artifacts`/`to_compose` tables (artifact_store.py / to_compose_selection.py)
and always has.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from algorand_shared.artifact_store import (
    COMPOSED,
    DISCARDED,
    PENDING,
    SELECTED,
    Artifact,
    ArtifactContent,
    get_artifact,
    get_artifact_content,
    mark_artifact_status,
)
from algorand_shared.to_compose_selection import (
    list_to_compose_for_day,
    select_to_compose_for_day,
)
from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery_app
from app.core import config
from app.core.redis_lock import single_flight
from app.modules.ai.mistral_credit_guard import is_credit_exhausted
from app.modules.newspaper.publish_fanout import fanout_after_publish
from app.modules.newspaper.publish_policy import (
    PublishKind,
    PublishTier,
    evaluate_standard_publish,
    remaining_standard_publish_slots,
)
from app.modules.newspaper.publish_queue_store import QueuedPublishRow, is_terminal_outcome
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


def _source_dead(row: QueuedPublishRow) -> bool:
    """True when the row's own source URL now reads as a parked/expired registrar page -- catches a source that died in the window between selection and compose (which can be hours to days: to_compose is a fixed daily slate, and compose is currently paused platform-wide).

    Complements, not duplicates, `defunct_entity_gate` (which checks DNS
    resolution of LINKS INSIDE THE WRITTEN BODY, after a full compose pass)
    and `domain_probe` (writer-enrichment, advisory-only, doesn't catch a
    parked page since it returns a normal 200/HTTPS). Root-caused
    2026-08-27: arima.io's registration expired after being crawled with
    real content, and nothing would have stopped it from being composed as
    if the project were still current. Runs LAST in the gate order
    deliberately -- unlike every other gate here, this one makes a real
    network call, so it should only ever run after every cheaper local
    check has had a chance to veto first.
    """
    from app.modules.newspaper.source_liveness import is_source_parked_or_expired

    return bool(row.scrape_url and is_source_parked_or_expired(row.scrape_url))


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
# review draft still re-covers the same project), novelty runs before the
# one real network call in this list so a capped/cooling row doesn't spend a
# Typesense query, and source_dead runs LAST of all -- it's the only gate
# here that makes a live HTTP request, so every cheaper local check gets a
# chance to veto first.
_PRE_COMPOSE_GATES: tuple[_DrainGate, ...] = (
    # First: an assignment for a brief that's since been archived must never
    # compose — cheap, decisive, and drops the stale row out of pending.
    _DrainGate("brief_archived", _brief_archived, mark_status="expired"),
    # No mark_status (unlike brief_archived/novelty_collapsed): a domain-cap
    # collision is transient by definition -- it clears at midnight, not
    # because anything about the artifact was wrong. Discarding it here used
    # to drop its accumulated content outright (supersede-concatenation only
    # merges from PENDING artifacts, so a future crawl of the same domain
    # would start a fresh artifact from scratch). Leaving it SELECTED costs
    # nothing (later runs today just re-hit the same cap check cheaply) and
    # lets the midnight `reclaim_stale_selected_artifacts` beat recycle it
    # back to PENDING once its day has passed, so it re-competes normally
    # instead of being permanently lost (root-caused 2026-08-27).
    _DrainGate("domain_capped", _domain_capped),
    _DrainGate("domain_cooldown", _domain_in_cooldown),
    _DrainGate("service_cooldown", _service_in_cooldown),
    _DrainGate("novelty_collapsed", _novelty_collapsed, mark_status="expired"),
    # A parked/expired domain isn't coming back on its own -- permanent
    # discard, same as brief_archived/novelty_collapsed above.
    _DrainGate("source_dead", _source_dead, mark_status="expired"),
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


def _release_due_backlog(slots: int) -> dict | None:
    """Release an admin-approved backlog item first (held because the cap was full when it was approved) — cheap, no compose cost, and shares this exact pacing gate/budget with composing something new below (folded in from the old standalone drain_approved_feed_queue task/beat entry). Only gates THIS step on the interval, same as before — review composition below intentionally bypasses publish pacing (it doesn't hit the feed until approved), so a blanket check up here would wrongly delay it too. Returns a terminal drain result when something was released, else None to fall through to fresh composition."""
    # Local (not module-level) import so a test's
    # monkeypatch.setattr("...publish_schedule.is_standard_publish_due", ...)
    # is actually observed here -- a module-level `from ... import` binds its
    # own name in THIS module's namespace once at import time and would not
    # see a later patch applied to publish_schedule's own attribute.
    from app.modules.newspaper.publish_schedule import is_standard_publish_due

    due, _detail = is_standard_publish_due()
    if not due:
        return None
    backlog = _release_pending_feed_backlog(slots=slots)
    if backlog.get("published", 0):
        return {**backlog, "tier": "standard", "source": "pending_feed_backlog"}
    return None


def _process_review_row(
    row: QueuedPublishRow,
    *,
    review_full: bool,
    backlog_full: bool,
    reviews_composed: int,
    resolve: Callable[[QueuedPublishRow, dict], str],
) -> tuple[dict | None, bool, int, int]:
    """Compose a review-bound row (or skip it for this run). Returns (results_entry_or_None, updated_review_full, updated_reviews_composed, published_delta).

    ``resolve`` is the row's compose-outcome resolver -- always a closure
    over ``_resolve_artifact`` (see ``_drain_one_to_compose_slot``), injected
    rather than called directly so this implementation stays independent of
    how the artifact is identified/resolved.
    """
    if review_full or reviews_composed >= config.REVIEW_COMPOSE_BATCH_LIMIT:
        return None, review_full, reviews_composed, 0
    if backlog_full:
        # A full day of releases is already queued — composing more now is
        # pure cost. Rows stay pending; composing resumes once the paced
        # release drains the backlog below the cap.
        return None, review_full, reviews_composed, 0

    from app.modules.crawler.classifier_review_store import review_queue_full

    outcome = publish_from_queued_row(row, publish_tier=PublishTier.STANDARD)
    outcome_status = resolve(row, outcome)
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
    row: QueuedPublishRow,
    published: int,
    *,
    backlog_full: bool,
    resolve: Callable[[QueuedPublishRow, dict], str],
) -> tuple[dict | None, int, dict | None]:
    """Evaluate policy and compose+publish one non-review standard row. Returns (results_entry, published_delta, early_stop_result). See `_process_review_row` for what ``resolve`` is.

    ``backlog_full`` mirrors `_process_review_row`'s own check: a full day of
    paced backlog releases already queued means composing a fresh standard
    row now is pure cost (it would only join the same backlog, further
    behind). The review branch already gets this right -- this was the one
    remaining path that didn't, so a standard-tier slot behind an already-
    full backlog composed anyway while the review branch waited.
    """
    if backlog_full:
        return None, 0, None
    kind = PublishKind(row.publish_kind)
    diff = row.payload.get("diff")
    decision = evaluate_standard_publish(
        kind, diff=diff, source_kind=row.payload.get("source_kind")
    )
    if not decision.allowed:
        return None, 0, {"status": "skipped", "reason": decision.reason, "published": 0}

    outcome = publish_from_queued_row(row, publish_tier=PublishTier.STANDARD)
    status = resolve(row, outcome)
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


def _today_str() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Artifact-native adapter + resolver -- lets every gate/compose helper above
# (written against QueuedPublishRow) run unmodified against an editorial-room
# artifact.
# --------------------------------------------------------------------------- #


def _artifact_to_queued_row(
    artifact: Artifact, content: ArtifactContent | None
) -> QueuedPublishRow:
    """Reconstruct the exact QueuedPublishRow-shape publish_from_queued_row (and every _PRE_COMPOSE_GATES check) expects, from an artifact + its stashed content. ``payload`` is the SAME dict ingest_signal.py/editorial_assignment.py built at signal time (content.metadata["payload"]). ``queue_id`` is set to the artifact_id itself -- it is only ever used as a single_flight lock key inside publish_from_queued_row (harmless to share a redis-key namespace with historical real queue_ids -- different UUID, never collides)."""
    meta = (content.metadata or {}) if content else {}
    payload = dict(meta.get("payload") or {})
    payload.setdefault("page_text", content.content if content else "")
    payload.setdefault("page_title", content.title if content else "")
    display_name = str(
        meta.get("display_name") or (content.title if content else "") or artifact.service_id or ""
    )
    created_at = artifact.created_at
    return QueuedPublishRow(
        queue_id=artifact.artifact_id,
        priority=int(artifact.priority),
        topic=str(payload.get("topic", "")),
        publish_kind=str(payload.get("publish_kind", "")),
        service_id=artifact.service_id or "",
        display_name=display_name,
        scrape_url=artifact.url or "",
        payload=payload,
        created_at_epoch=int(created_at.timestamp()) if created_at else 0,
        human_pick_day=artifact.human_pick_day,
    )


def _resolve_artifact(artifact_id: str, outcome: dict) -> str:
    """Resolve an artifact's compose outcome, returning the outcome status string.

    - a TERMINAL outcome (published/review/approved_backlog/duplicate/...,
      see publish_queue_store.TERMINAL_OUTCOMES -- reused as-is, the
      resolving-outcome vocabulary is a property of publish_from_queued_row's
      return shape, not of publish_queue) marks the artifact COMPOSED;
    - an outcome carrying ``queue_status`` (e.g. the content-quality veto's
      "expired") is a permanent drop -- marks the artifact DISCARDED;
    - anything else (rate_limited, mistral_failed, already_running,
      review_queue_full, ...) is transient -- the artifact is left SELECTED
      so a later drain_to_compose run retries it.

    ``outcome`` here always reflects a compose that already ran (paid for),
    so the marking write below must itself land -- retried once if
    ``SoftTimeLimitExceeded`` fires mid-write (the Cassandra call), then
    re-raised. Without the retry, an artifact whose compose already
    succeeded (published/held for review) could get its bookkeeping write
    cut off by the same soft-limit interrupt that also killed the drain
    run, leaving it stuck SELECTED and re-composed by a later run even
    though its work already landed.
    """
    status = str(outcome.get("status", ""))
    queue_status = str(outcome.get("queue_status", ""))

    def _mark() -> None:
        if is_terminal_outcome(outcome):
            mark_artifact_status(artifact_id, COMPOSED)
        elif queue_status:
            mark_artifact_status(artifact_id, DISCARDED)

    try:
        _mark()
    except SoftTimeLimitExceeded:
        logger.warning(
            "artifact %s bookkeeping write interrupted by soft time limit — retrying once",
            artifact_id,
        )
        _mark()
        raise

    return status


def _resolve_artifact_ignoring_row(artifact_id: str, _row: QueuedPublishRow, outcome: dict) -> str:
    """`_resolve_artifact` reshaped to the `Callable[[QueuedPublishRow, dict], str]` signature `_process_review_row`/`_publish_standard_row`'s `resolve` param expects -- drain_to_compose binds `artifact_id` via `functools.partial` once per slot, so each call only needs to pass (row, outcome). `_row` is unused: this resolver already knows which artifact it's resolving from the partial-bound argument, not from the row it's handed."""
    return _resolve_artifact(artifact_id, outcome)


def _record_pre_compose_gate_artifact(artifact_id: str, fired: _DrainGate) -> dict:
    """Artifact-native twin of the old `_record_pre_compose_gate`: a gate with a `mark_status` (brief_archived/novelty_collapsed/source_dead) is a permanent drop for today's slate -- DISCARD. A gate with no `mark_status` (domain_cooldown/service_cooldown/domain_capped) leaves the artifact SELECTED/retriable -- nothing to persist beyond this run's results entry; domain_capped specifically relies on this to let the midnight reclaim beat recycle it to PENDING once its day passes, rather than losing it outright."""
    if fired.mark_status:
        mark_artifact_status(artifact_id, DISCARDED)
    return {"artifact_id": artifact_id, "status": fired.name}


def _ensure_today_selected(day: str) -> None:
    """Self-heal a missed/late `select_to_compose_for_today_task` beat: if `day`'s to_compose slate hasn't been picked yet, pick it now. A no-op the moment a row already exists for `day` -- cheap enough to call at the top of every drain_to_compose run."""
    if list_to_compose_for_day(day):
        return
    select_to_compose_for_day(day)


def _drain_to_compose_setup() -> tuple[int, dict | None]:
    """Slot budget and early-exit checks before composing anything: daily cap, admin-approved backlog release, credit exhaustion. Returns (slots, early_result) -- early_result is a terminal drain result the caller should return immediately, else None to proceed. Artifact-native twin of the old `_standard_drain_setup` (AUTO_COMPOSE_PAUSED is checked by the caller before this runs, same split as before).

    Backlog release never composes (it only publishes already-paid-for
    work), so it runs even while LLM credit is exhausted -- but after the
    daily cap check, since a capped day shouldn't even attempt a release.
    """
    slots = remaining_standard_publish_slots()
    if slots <= 0:
        return slots, {"status": "skipped", "reason": "standard_daily_cap_reached", "published": 0}

    backlog_result = _release_due_backlog(slots)
    if backlog_result is not None:
        return slots, backlog_result

    if is_credit_exhausted(config.LLM_PROVIDER_WRITER):
        return slots, {"status": "skipped", "reason": "mistral_credit_exhausted", "published": 0}

    return slots, None


@dataclass
class _ToComposeRunState:
    """Mutable state threaded through one drain_to_compose run's slot loop -- review-slot occupancy, this run's compose budget, and the accumulating results list.

    `published` is shared across BOTH the review and non-review branches
    (faithfully porting the old standard drain's identical shared counter):
    a review-bound slot's fresh-auto-approve can itself publish straight to
    the feed, and that ALSO counts against the one-fresh-compose-per-run
    budget a later non-review slot checks -- see _drain_one_to_compose_slot.
    """

    review_full: bool
    backlog_full: bool
    reviews_composed: int = 0
    published: int = 0
    results: list[dict[str, object]] = field(default_factory=list)


def _drain_one_to_compose_slot(
    slot_row: dict[str, object], state: _ToComposeRunState
) -> tuple[dict | None, bool]:
    """Process one to_compose slot, mutating `state` in place. Returns (early_stop_result, should_break): a non-None `early_stop_result` (rate_limited) is a terminal drain result the caller must return immediately; `should_break` means stop iterating the rest of this run's slate WITHOUT an error -- the one-fresh-compose-per-run budget is already spent."""
    artifact_id = str(slot_row["artifact_id"])
    artifact = get_artifact(artifact_id)
    if artifact is None or artifact.status != SELECTED:
        # Already composed/discarded on an earlier run for this same day's
        # slate, or a stale/bad reference -- nothing to do.
        return None, False
    content = get_artifact_content(artifact_id)
    row = _artifact_to_queued_row(artifact, content)
    resolve_this = functools.partial(_resolve_artifact_ignoring_row, artifact_id)

    fired = _run_pre_compose_gates(row)
    if fired is not None:
        state.results.append(_record_pre_compose_gate_artifact(artifact_id, fired))
        return None, False

    if _row_needs_review(row):
        entry, state.review_full, state.reviews_composed, published_delta = _process_review_row(
            row,
            review_full=state.review_full,
            backlog_full=state.backlog_full,
            reviews_composed=state.reviews_composed,
            resolve=resolve_this,
        )
        state.published += published_delta
        if entry is not None:
            state.results.append(entry)
        return None, False

    if state.published >= 1:
        # This run's shared compose/publish budget is already spent (by an
        # earlier non-review compose OR a review-branch fresh-auto-approve
        # that published straight to the feed) -- faithful port of the old
        # standard drain's own `if published >= 1: break`.
        return None, True
    entry, published_delta, early_stop = _publish_standard_row(
        row, state.published, backlog_full=state.backlog_full, resolve=resolve_this
    )
    if early_stop is not None:
        return early_stop, False
    state.published += published_delta
    if entry is not None:
        state.results.append(entry)
    return None, False


@celery_app.task(name="app.tasks.newspaper.select_to_compose_for_today")
def select_to_compose_for_today_task() -> dict[str, object]:
    """Daily beat: pick today's `to_compose` slate -- the human pin an admin set yesterday via "pin for tomorrow" (see artifact_store.pin_artifact_for_day / to_compose_selection.pin_for_tomorrow) plus N-1 top-priority platform picks. See to_compose_selection.select_to_compose_for_day for the full selection rule (including why an unpinned human slot is left empty, never backfilled).

    Guarded to be a no-op if `day`'s slate is already populated -- e.g. by
    an admin "Redo"/pin action taken for tomorrow before this beat rolls
    over into it. `select_to_compose_for_day` itself only DELETEs+re-picks
    (it does not revert already-SELECTED artifacts the way
    `reset_and_reselect_for_day` does), so calling it unconditionally on an
    already-populated day would silently drop the day's human pin (the
    selection scan only looks at PENDING artifacts) and strand its
    platform picks as SELECTED-with-no-to_compose-row -- invisible to both
    a later Redo (which only reverts what the day's *current* rows
    reference) and to `reclaim_stale_selected_artifacts` (which only scans
    `to_compose` rows). Root-caused 2026-08-27 after a review of this exact
    beat found it would have clobbered a live pin+platform slate the same
    night. Mirrors `_ensure_today_selected`'s existing no-op check.
    """
    day = _today_str()
    existing = list_to_compose_for_day(day)
    if existing:
        return {
            "status": "skipped",
            "reason": "already_selected",
            "compose_day": day,
            "existing_slots": len(existing),
        }
    return select_to_compose_for_day(day)


@celery_app.task(name="app.tasks.newspaper.reclaim_stale_selected_artifacts")
def reclaim_stale_selected_artifacts_task() -> dict[str, object]:
    """Beat: revert any artifact still SELECTED for a `to_compose` day that has already passed back to PENDING, so it re-enters normal ranking instead of staying permanently stranded.

    Root-caused live 2026-08-26 (see to_compose_selection.
    find_stale_selected_artifacts's own docstring for the full mechanism):
    `drain_to_compose` only ever composes TODAY's own slate, every run,
    forever -- a slot still SELECTED when its day rolls over (a gate
    cooldown that never cleared, review_queue_full all day, a
    soft-time-limit interruption near midnight, more slots than the day's
    compose budget could reach) is invisible to every future day's
    selection AND every future drain run alike. Found two real casualties
    from 2026-08-25's platform picks sitting exactly like this. Runs
    dry_run=False -- this beat's entire purpose is to actually reclaim, not
    just report; call to_compose_selection.find_stale_selected_artifacts
    directly for a read-only look first if you want to preview what a run
    would touch.
    """
    from algorand_shared.to_compose_selection import reclaim_stale_selected_artifacts

    return reclaim_stale_selected_artifacts(dry_run=False)


@celery_app.task(name="app.tasks.newspaper.discard_dead_pending_sources")
def discard_dead_pending_sources_task() -> dict[str, object]:
    """Beat: discard PENDING crawler-channel artifacts whose source has since become a parked/expired-registration page (see `source_liveness` module docstring).

    Catches this class of dead artifact BEFORE it can ever be selected --
    `_source_dead` (the pre-compose gate, `_PRE_COMPOSE_GATES`) only catches
    it AFTER selection, in the narrower window between a day's slate being
    picked and drain actually reaching that slot. Both exist because either
    alone leaves a gap: this beat never runs on an artifact once it's
    SELECTED (selection has moved on), and the gate never runs on an
    artifact that's never selected at all.

    A small `scan_limit` per run (unlike most other reconciliation sweeps in
    this codebase) is deliberate: each check is a REAL network fetch with an
    8s timeout, on a box that also runs other latency-sensitive services
    (see project notes on shared-box CPU/network contention) -- this must
    stay a slow trickle across many runs, not a one-shot sweep of the whole
    pending pool. Runs dry_run=False -- this beat's entire purpose is to
    actually discard, not just report; call
    `source_liveness.find_dead_pending_artifacts` directly for a read-only
    preview first if you want to see what a run would touch.
    """
    from app.modules.newspaper.source_liveness import discard_dead_pending_artifacts

    return discard_dead_pending_artifacts(scan_limit=15, dry_run=False)


@celery_app.task(
    name="app.tasks.newspaper.drain_to_compose",
    soft_time_limit=config.COMPOSE_TASK_SOFT_TIME_LIMIT,
    time_limit=config.COMPOSE_TASK_TIME_LIMIT,
)
@single_flight(lambda: "drain:to_compose", ttl=config.COMPOSE_TASK_TIME_LIMIT)
def drain_to_compose() -> dict[str, object]:
    """Compose today's already-selected `to_compose` slate -- the live successor to drain_standard_publish_queue (see this module's docstring for what changed and why).

    single_flight-locked on a single fixed key (no per-row/per-day
    parameterization -- this task itself takes no arguments) with a TTL
    pinned to COMPOSE_TASK_TIME_LIMIT (the HARD kill bound, not just the
    soft one) so the lock always outlives the run it guards even if the
    soft limit's interrupt doesn't land in time and celery has to hard-kill
    the worker -- an overlapping second run would double-process the same
    to_compose slate (concurrent compose of the same slot, or two runs
    racing the same one-fresh-compose-per-run budget).

    Cadence: `select_to_compose_for_today_task` picks the day's fixed slate
    ONCE (a dedicated daily beat, self-healed here too via
    `_ensure_today_selected` in case that beat is late/missed); THIS task
    then runs on a tighter cadence (same beat interval the old standard
    drain used) and composes eligible slots from that fixed slate --
    review-bound slots up to REVIEW_COMPOSE_BATCH_LIMIT per run (unpaced,
    same as before: a held draft doesn't hit the feed until approved), and
    at most ONE fresh non-review compose per run, paced by
    evaluate_standard_publish's own is_standard_publish_due() check (so N
    articles are never fired simultaneously the moment they're selected).
    A slot a gate defers (cooldown, or domain_capped -- transient, clears
    at midnight) or that hits review_queue_full stays SELECTED and is
    retried on a later run within the same day (domain_capped specifically
    relies on staying SELECTED past midnight too, so the reclaim beat can
    recycle it to PENDING once its day passes rather than losing it
    outright); a slot a gate permanently drops (novelty_collapsed/
    brief_archived/source_dead) is DISCARDED and — matching to_compose_selection's own
    no-backfill design for an unused human slot — is NOT replaced by the
    next-best pending artifact this run; that slot is simply lost for the
    day. This mirrors an existing, deliberate limitation of the
    (already-built, already-tested) selection design, not a new gap
    introduced here.
    """
    if config.AUTO_COMPOSE_PAUSED:
        return {"status": "skipped", "reason": "auto_compose_paused", "published": 0}

    slots, early_result = _drain_to_compose_setup()
    if early_result is not None:
        return early_result

    today = _today_str()
    _ensure_today_selected(today)
    slate = list_to_compose_for_day(today)
    if not slate:
        return {
            "status": "ok",
            "tier": "standard",
            "published": 0,
            "results": [],
            "reason": "no_selection_for_today",
        }

    from app.modules.crawler.classifier_review_store import review_queue_full

    state = _ToComposeRunState(
        review_full=review_queue_full(), backlog_full=_pending_feed_backlog_full()
    )

    try:
        for slot_row in sorted(slate, key=lambda r: r["slot"]):
            early_stop, should_break = _drain_one_to_compose_slot(slot_row, state)
            if early_stop is not None:
                return {**early_stop, "compose_day": today, "results": state.results}
            if should_break:
                # Faithful port of the old standard drain's own `break`: once
                # this run has spent its one fresh non-review compose (or
                # decided it can't), remaining slots -- review-bound or not
                # -- wait for the next run rather than being skipped-and-
                # continued past.
                break
    except SoftTimeLimitExceeded:
        # Killed mid-compose despite the budget guard: return partial
        # progress. The in-flight artifact was never marked done, so it
        # stays SELECTED/retriable.
        return {
            "status": "interrupted",
            "tier": "standard",
            "reason": "soft_time_limit",
            "compose_day": today,
            "published": state.published,
            "results": state.results,
        }

    return {
        "status": "ok",
        "tier": "standard",
        "compose_day": today,
        "published": state.published,
        "slots_remaining_start": slots,
        "results": state.results,
    }


@celery_app.task(
    name="app.tasks.newspaper.compose_artifact_now",
    soft_time_limit=config.COMPOSE_TASK_SOFT_TIME_LIMIT,
    time_limit=config.COMPOSE_TASK_TIME_LIMIT,
)
def compose_artifact_now(artifact_id: str) -> dict[str, object]:
    """Admin/editorial-triggered immediate compose of one artifact, bypassing drain_to_compose's pacing entirely. Used today by editorial_assignment.py's "compose this brief right now" trigger; a future admin Queue-tab action can dispatch it directly by artifact_id.

    Refuses an artifact that isn't PENDING or SELECTED (already composed/
    discarded, or an unknown id) so a stale/duplicate trigger can't
    double-compose.
    """
    artifact = get_artifact(artifact_id)
    if artifact is None:
        return {"status": "error", "reason": "artifact_not_found"}
    if artifact.status not in (PENDING, SELECTED):
        return {
            "status": "error",
            "reason": f"artifact not pending/selected (status={artifact.status!r}) — refused",
        }

    content = get_artifact_content(artifact_id)
    row = _artifact_to_queued_row(artifact, content)
    outcome = publish_from_queued_row(row)
    _resolve_artifact(artifact_id, outcome)
    return outcome


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


@celery_app.task(name="app.tasks.newspaper.reap_orphaned_browser_processes")
def reap_orphaned_browser_processes() -> dict[str, object]:
    """Maintenance beat: SIGKILL any Playwright/Chromium process tree with no live celery worker ancestor (see browser_reaper.py's module docstring for the root cause -- a forceful worker kill, e.g. a hard time_limit or a deploy's SIGQUIT cold shutdown, never signals the browser subprocess at all)."""
    from app.modules.scraper.core.browser_reaper import reap_orphaned_browser_processes as _reap

    return _reap(min_age_seconds=config.BROWSER_REAP_MIN_AGE_SECONDS)


def _release_pending_feed_backlog(*, slots: int) -> dict[str, object]:
    """Release ONE admin-approved article that was held because the daily feed cap was already reached (status='backlog'), interest order.

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
    from algorand_shared.article_transitions import list_backlog_articles, transition_article_status

    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    # One per run — the standard-publish interval pacing keeps releases at
    # NEWS_STANDARD_INTERVAL_HOURS apart regardless of which path releases them.
    # Ordering comes from articles.status='backlog' (article-table
    # consolidation Phase 4/5).
    backlog = list_backlog_articles()
    rows = backlog[:1]
    published = 0
    for r in rows:
        # 2026-08-25 (Phase 5): reads `articles` directly (was
        # articles_by_id's GET_FOR_FEED) -- summary/tags/image_url/
        # source_url/published_at all live on the same consolidated row now.
        art = session.execute(ArticlesStmts.GET_FULL_BY_ID, (r.article_id,)).one()
        if art is None:
            # A permanently missing article would otherwise jam this
            # one-row-per-run backlog forever: list_backlog_articles keeps
            # returning the SAME top-priority row every run (rows =
            # backlog[:1]) since nothing about it ever changes, so the
            # drain would retry -- and log -- the identical dead row
            # indefinitely instead of ever reaching the next-best backlog
            # candidate. Move it to a terminal status via the shared
            # transition helper so it drops out of list_backlog_articles's
            # status='backlog' scan and the next run advances.
            transitioned = transition_article_status(r.article_id, new_status="discarded_missing")
            logger.warning(
                "backlog row %s has no matching article — %s",
                r.article_id,
                "marked discarded_missing"
                if transitioned
                else "transition_article_status ALSO found no row; still stuck",
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
            # Article.publish() (2026-08-27): does the same backlog->published
            # transition + slug claim this used to do by hand
            # (transition_article_status + _claim_slug_for_feed), PLUS the
            # one thing that was missing -- refuses if a DIFFERENT article_id
            # already owns a live published article for this service_id
            # (e.g. a same-service article went live from a different path
            # while this one sat in backlog). See algorand_shared.article's
            # own docstring for the incident this closes.
            from algorand_shared.article import Article, DuplicateArticleError

            try:
                article = Article.load(art.article_id)
                if article is None:
                    logger.warning(
                        "backlog release: %s vanished between read and publish", art.article_id
                    )
                    release_publish_slot(tier=PublishTier.STANDARD)
                    break
                article.publish(new_published_at=released_at)
            except DuplicateArticleError:
                # A genuine business-rule refusal, not a store failure --
                # hand the slot back and stop this run without crashing the
                # beat; the next run will pick a different backlog row (or
                # the same one, if an admin resolves the conflict first).
                release_publish_slot(tier=PublishTier.STANDARD)
                break
            except Exception:
                # The article never became visible — hand the slot back so
                # the day's budget isn't burned by a failed write.
                release_publish_slot(tier=PublishTier.STANDARD)
                raise
            published += 1
            record_standard_publish()
            # Same "an article just went live" fanout the direct-publish path
            # uses (search index, translations, IndexNow, distribution) --
            # W4-A: this release path used to reimplement everything except
            # the search-index step, so a backlog release silently never put
            # the article into Typesense until the once-daily reindex safety
            # net caught it.
            fanout_after_publish(str(art.article_id), distribute=True)
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


# ensure_review_ready (the old "keep exactly one composed article waiting in
# review at all times" beat) is retired -- its purpose is now served by
# drain_to_compose itself: every run composes eligible review-bound slots
# from the day's already-selected slate (up to REVIEW_COMPOSE_BATCH_LIMIT,
# unpaced) on the SAME tight cadence ensure_review_ready used to run on, so a
# separate dedicated task doing the identical thing against a DIFFERENT data
# source (publish_queue) would double-compose. Same consolidation shape this
# codebase already used once for drain_approved_feed_queue -> folded into
# drain_standard_publish_queue (2026-07-14, see _release_due_backlog above).
