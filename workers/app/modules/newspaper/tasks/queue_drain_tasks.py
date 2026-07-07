from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery_app
from app.core import config
from app.core.feed_bucket import feed_month as _feed_month
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
    is_terminal_outcome,
    list_pending_queue,
    mark_queue_done,
    mark_queue_status,
    queue_row_tier,
    record_queue_reason,
)
from app.modules.newspaper.publish_schedule import record_standard_publish
from app.modules.newspaper.tasks.publish_tasks import publish_from_queued_row


def _row_needs_review(row) -> bool:
    """Would the classifier hold this row for admin review? Review-bound items
    bypass publish pacing — they don't hit the feed until approved, so spacing
    them out only delays training feedback. Reads the signals computed once at
    ingest; recomputes only for rows queued before signals existed."""
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


def _domain_capped(row) -> bool:
    """True when this web source already created its allotted articles today
    (COMPOSE_MAX_PER_DOMAIN_PER_DAY). Such rows are deferred rather than composed."""
    from app.modules.crawler.domain_tracker import domain_compose_cap_reached
    from app.modules.newspaper.tasks.publish_tasks import _compose_domain_for_row

    dom = _compose_domain_for_row(row)
    return bool(dom and domain_compose_cap_reached(dom))


def _domain_in_cooldown(row) -> bool:
    """True when this web source published/composed within the diversity cooldown
    (COMPOSE_DOMAIN_COOLDOWN_HOURS). Unlike the daily cap, cooldown is short-lived,
    so such rows are left pending (not deferred) to retry once it clears."""
    from app.modules.crawler.domain_tracker import domain_in_cooldown
    from app.modules.newspaper.tasks.publish_tasks import _compose_domain_for_row

    dom = _compose_domain_for_row(row)
    return bool(dom and domain_in_cooldown(dom))


def _service_in_cooldown(row) -> bool:
    """True when this SERVICE (across all its domains) published/composed within
    its diversity cooldown. Complements _domain_in_cooldown for a project whose
    domains don't share a registrable domain, so the per-domain check alone
    can't see the repeat (e.g. a project's own site + a separate Medium blog)."""
    from app.modules.crawler.domain_tracker import service_in_cooldown

    return bool(row.service_id and service_in_cooldown(row.service_id))


def _novelty_collapsed(row) -> bool:
    """Fresh novelty at drain time. The enqueue-time novelty is a snapshot; a
    story on the same subject may have PUBLISHED after this row entered the
    queue (the Defly case: a newsletter about a wallet we had just covered), so
    the stored priority silently overstates it. Recompute against articles
    published NOW and cut the row when novelty has collapsed — one cheap
    Typesense query beats a six-minute duplicate compose. Fails open (0.0
    similarity → not collapsed) when Typesense is unavailable.

    Uses the SAME boundary as the compose-time duplicate check
    (NOVELTY_MAX_SIMILARITY, publish_tasks.py) rather than the old, more
    lenient NOVELTY_DUPLICATE_FLOOR — a row in between the two used to survive
    this drain-time check only to be discarded as a duplicate mid-compose,
    wasting a full Mistral call."""
    from app.modules.newspaper.article_grader import (
        recent_content_similarity,
        recent_title_similarity,
    )

    title = str(row.payload.get("page_title", ""))
    text = str(row.payload.get("page_text", ""))
    title_sim, _ = recent_title_similarity(title)
    content_sim, _ = recent_content_similarity(title, text)
    closest_sim = max(title_sim, content_sim)
    return closest_sim >= config.NOVELTY_MAX_SIMILARITY


@dataclass(frozen=True)
class _DrainGate:
    """One pre-compose veto in the standard drain: ``check(row)`` True means the
    row is skipped this run with ``name`` as its reported status. ``mark_status``
    moves the row out of the pending lane (``deferred``/``expired``); None leaves
    it pending to retry next beat (right for short-lived cooldowns)."""

    name: str
    check: Callable[..., bool]
    mark_status: str | None = None


# Evaluated in order, first match wins. The order is load-bearing:
# cooldown checks MUST run before the review branch in the drain loop (a
# review draft still re-covers the same project), and novelty runs last so a
# capped/cooling row doesn't spend a Typesense query.
_PRE_COMPOSE_GATES: tuple[_DrainGate, ...] = (
    _DrainGate("domain_capped", _domain_capped, mark_status="deferred"),
    _DrainGate("domain_cooldown", _domain_in_cooldown),
    _DrainGate("service_cooldown", _service_in_cooldown),
    _DrainGate("novelty_collapsed", _novelty_collapsed, mark_status="expired"),
)


def _run_pre_compose_gates(row) -> _DrainGate | None:
    """First gate that vetoes this row, or None when all pass.

    Checks are resolved through the module namespace at call time (not the
    reference frozen into the tuple at import) so monkeypatching e.g.
    ``queue_drain_tasks._domain_in_cooldown`` keeps working — that seam is how
    the existing drain tests fake cooldown/cap state."""
    for gate in _PRE_COMPOSE_GATES:
        check = globals().get(gate.check.__name__, gate.check)
        if check(row):
            return gate
    return None


def _resolve(row, outcome: dict) -> str:
    """Mark the queue row done when its compose outcome resolved it (published /
    review / duplicate); leave it pending otherwise. Returns the status string.
    Single source of truth for which outcomes dequeue a row — a status missing
    from TERMINAL_OUTCOMES is the bug class behind 'the same topic reappears'.
    Either way the outcome is persisted as the row's last_reason, so the admin
    queue view can answer "why is/was this row here"."""
    status = str(outcome.get("status", ""))
    reason = str(outcome.get("reason", "") or status)
    if is_terminal_outcome(outcome):
        mark_queue_done(row.queue_id, reason=reason)
    elif status:
        record_queue_reason(row.queue_id, reason)
    return status


def _compose_review_row(row) -> dict:
    """Compose one review-bound row at standard tier and resolve it. Shared by
    the standard drain's review branch and ensure_review_ready."""
    outcome = publish_from_queued_row(row, publish_tier=PublishTier.STANDARD)
    _resolve(row, outcome)
    return outcome


@dataclass
class _BreakingVetoCtx:
    """Loop state the breaking vetoes need beyond the row: whether the single
    review slot is occupied this run, and the credibility assessment — set by
    the credibility veto (last gate) and reused by the drain to tag the
    SUCCESS outcome with its method, so it's only computed once and only for
    rows that reach that gate. Mutable on purpose (unlike _DrainGate rows)."""

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
    """Composing a review-bound item into a full review queue just returns
    "review_queue_full" — a status the drain does NOT mark done, so the row
    would recompose every beat, burning a full Mistral loop each time. Leave
    it pending until the admin clears the review queue."""
    if ctx.review_full and _row_needs_review(ctx.row):
        return {"status": "skipped", "reason": "review_queue_full"}
    return None


def _breaking_credibility_veto(ctx: _BreakingVetoCtx) -> dict | None:
    """Breaking must be corroborated (alert keywords + evidence), or it waits."""
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
        }
    return None


# Evaluated in order, first non-None outcome wins — same pattern as the
# standard drain's _PRE_COMPOSE_GATES and compose-side _PRE_COMPOSE_VETOES.
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


@celery_app.task(name="app.tasks.newspaper.drain_breaking_publish_queue")
def drain_breaking_publish_queue() -> dict[str, object]:
    """Publish breaking-tier items immediately up to the separate daily cap."""
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
                record_queue_reason(row.queue_id, str(veto_outcome.get("reason", "skipped")))
                results.append({"queue_id": row.queue_id, **veto_outcome})
                continue

            # Breaking news (scams/incidents) is urgent and rare — exempt from
            # the per-website daily article cap so a critical alert is never held.
            outcome = publish_from_queued_row(
                row, publish_tier=PublishTier.BREAKING, enforce_domain_cap=False
            )
            status = _resolve(row, outcome)
            if status == "published":
                published += 1
            elif status in ("review", "duplicate", "duplicate_review_pending"):
                # Filling the review slot closes it for the rest of this run.
                review_full = review_queue_full()
            elif status == "rate_limited":
                return {
                    "status": "skipped",
                    "reason": outcome.get("reason", "rate_limited"),
                    "published": published,
                    "results": results,
                }
            # ctx.assessment is guaranteed set: the credibility veto (last
            # gate) ran and passed for any row that reaches a compose.
            results.append(
                {"queue_id": row.queue_id, **outcome, "credibility": ctx.assessment.method}
            )
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


@celery_app.task(name="app.tasks.newspaper.drain_standard_publish_queue")
def drain_standard_publish_queue() -> dict[str, object]:
    """Publish standard-tier items on the ~3h schedule, up to 7/day."""
    slots = remaining_standard_publish_slots()
    if slots <= 0:
        return {"status": "skipped", "reason": "standard_daily_cap_reached", "published": 0}

    pending = _pending_for_tier(PublishTier.STANDARD, limit=config.PUBLISH_QUEUE_BATCH_LIMIT)
    published = 0
    results: list[dict[str, str]] = []

    from app.modules.crawler.classifier_review_store import review_queue_full

    # If the review slot is already full (admin hasn't acted on the pending item),
    # composing more review-bound articles just burns full Mistral agentic loops on
    # a result that gets discarded with "review_queue_full". Skip them until the
    # admin clears the queue; publish-worthy items still flow below.
    review_full = review_queue_full()
    reviews_composed = 0
    try:
        for row in pending:
            # Pre-compose vetoes (_PRE_COMPOSE_GATES): per-website daily cap,
            # domain/service diversity cooldowns, and the drain-time duplicate
            # cut, in that order. All run BEFORE the review branch below (a
            # review draft still re-covers the same project/story).
            fired = _run_pre_compose_gates(row)
            if fired is not None:
                if fired.mark_status:
                    mark_queue_status(row.queue_id, fired.mark_status, reason=fired.name)
                else:
                    record_queue_reason(row.queue_id, fired.name)
                results.append({"queue_id": row.queue_id, "status": fired.name})
                continue

            if _row_needs_review(row):
                if review_full or reviews_composed >= config.REVIEW_COMPOSE_BATCH_LIMIT:
                    continue
                outcome = _compose_review_row(row)
                if outcome.get("status") == "review":
                    reviews_composed += 1
                    # The slot we just filled may now be full (MAX_PENDING_REVIEWS
                    # is typically 1) — re-check so we don't compose more reviews
                    # this run only to discard them with "review_queue_full".
                    review_full = review_queue_full()
                results.append({"queue_id": row.queue_id, **outcome})
                continue

            if published >= 1:
                break
            kind = PublishKind(row.publish_kind)
            diff = row.payload.get("diff")
            decision = evaluate_standard_publish(
                kind, diff=diff, source_kind=row.payload.get("source_kind")
            )
            if not decision.allowed:
                return {
                    "status": "skipped",
                    "reason": decision.reason,
                    "published": 0,
                    "results": results,
                }

            outcome = publish_from_queued_row(row, publish_tier=PublishTier.STANDARD)
            status = _resolve(row, outcome)
            if status == "published":
                record_standard_publish()
                published += 1
            elif status == "rate_limited":
                return {
                    "status": "skipped",
                    "reason": outcome.get("reason", "rate_limited"),
                    "published": published,
                    "results": results,
                }
            results.append({"queue_id": row.queue_id, **outcome})
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
    """Maintenance beat: mark any compose_sessions row stuck researching/writing
    past the staleness window as "stale" (see tool_insights_store for why one
    can get orphaned there)."""
    from app.modules.ai.tool_insights_store import reap_stale_compose_sessions as _reap

    return _reap()


@celery_app.task(name="app.tasks.newspaper.expire_stale_queue_items")
def expire_stale_queue_items() -> dict[str, object]:
    """
    Queue maintenance (Phase 5):
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


@celery_app.task(name="app.tasks.newspaper.drain_publish_queue")
def drain_publish_queue() -> dict[str, object]:
    """Legacy entry: run breaking then standard drains."""
    breaking = drain_breaking_publish_queue()
    standard = drain_standard_publish_queue()
    return {
        "status": "ok",
        "breaking": breaking,
        "standard": standard,
        "published": int(breaking.get("published", 0)) + int(standard.get("published", 0)),
    }


@celery_app.task(name="app.tasks.newspaper.drain_approved_feed_queue")
def drain_approved_feed_queue() -> dict[str, object]:
    """Release admin-approved articles that were held because the daily feed
    cap was already reached, up to the remaining 7/day slots (interest order)."""
    from datetime import UTC, datetime

    from app.core import config as cfg
    from app.core.cassandra import get_cassandra_session
    from app.modules.newspaper.article_store import insert_stored_article  # noqa: F401
    from app.modules.newspaper.publish_policy import remaining_standard_publish_slots

    slots = remaining_standard_publish_slots()
    if slots <= 0:
        return {"status": "skipped", "reason": "daily_cap_reached", "published": 0}

    from app.modules.newspaper.publish_schedule import feed_release_due, record_feed_release

    due, remaining = feed_release_due(min_gap_seconds=cfg.APPROVED_FEED_MIN_GAP_SECONDS)
    if not due:
        return {"status": "skipped", "reason": f"min_gap ({remaining}s remaining)", "published": 0}

    from app.core.statements import ArticleStmts, FeedStmts, PendingFeedStmts

    session = get_cassandra_session()
    bucket = getattr(cfg, "NEWS_FEED_BUCKET", "main") or "main"
    # One per run — the min-gap pacing keeps releases at most one per hour.
    rows = list(session.execute(PendingFeedStmts.PEEK, (bucket,)))
    published = 0
    for r in rows:
        art = session.execute(ArticleStmts.GET_FOR_FEED, (r.article_id,)).one()
        if art is not None:
            session.execute(
                FeedStmts.INSERT_BASIC,
                (
                    _feed_month(art.published_at or datetime.now(tz=UTC)),
                    art.published_at or datetime.now(tz=UTC),
                    art.article_id,
                    art.service_id,
                    art.title,
                    art.summary or "",
                    list(art.tags or []),
                ),
            )
            published += 1
            record_feed_release()
            from app.modules.newspaper.tasks.publish_tasks import (
                enqueue_article_translations,
            )

            enqueue_article_translations(str(art.article_id))
        session.execute(
            PendingFeedStmts.DELETE,
            (r.bucket, r.interest_score, r.approved_at, r.article_id),
        )
    return {"status": "ok", "published": published, "slots": slots}


@celery_app.task(name="app.tasks.newspaper.ensure_review_ready")
def ensure_review_ready() -> dict[str, object]:
    """Keep exactly one composed article waiting in the review queue at all
    times (when candidates exist), so the admin always has one to act on."""
    from app.modules.crawler.classifier_review_store import review_queue_full

    if review_queue_full():
        return {"status": "skipped", "reason": "review_queue_full"}
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
