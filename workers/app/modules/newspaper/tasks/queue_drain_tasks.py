from __future__ import annotations

from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery_app
from app.core import config
from app.core.feed_bucket import feed_month as _feed_month
from app.modules.newspaper.breaking_credibility import assess_breaking_credibility
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


def _novelty_collapsed(row) -> bool:
    """Fresh novelty at drain time. The enqueue-time novelty is a snapshot; a
    story on the same subject may have PUBLISHED after this row entered the
    queue (the Defly case: a newsletter about a wallet we had just covered), so
    the stored priority silently overstates it. Recompute against articles
    published NOW and cut the row when novelty has collapsed — one cheap
    Typesense query beats a six-minute duplicate compose. Fails open (0.0
    similarity → not collapsed) when Typesense is unavailable."""
    from app.modules.newspaper.article_grader import (
        recent_content_similarity,
        recent_title_similarity,
    )

    title = str(row.payload.get("page_title", ""))
    text = str(row.payload.get("page_text", ""))
    title_sim, _ = recent_title_similarity(title)
    content_sim, _ = recent_content_similarity(title, text)
    novelty = 1.0 - max(title_sim, content_sim)
    return novelty <= config.NOVELTY_DUPLICATE_FLOOR


def _resolve(row, outcome: dict) -> str:
    """Mark the queue row done when its compose outcome resolved it (published /
    review / duplicate); leave it pending otherwise. Returns the status string.
    Single source of truth for which outcomes dequeue a row — a status missing
    from TERMINAL_OUTCOMES is the bug class behind 'the same topic reappears'."""
    if is_terminal_outcome(outcome):
        mark_queue_done(row.queue_id)
    return str(outcome.get("status", ""))


def _compose_review_row(row) -> dict:
    """Compose one review-bound row at standard tier and resolve it. Shared by
    the standard drain's review branch and ensure_review_ready."""
    outcome = publish_from_queued_row(row, publish_tier=PublishTier.STANDARD)
    _resolve(row, outcome)
    return outcome


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
            kind = PublishKind(row.publish_kind)
            diff = row.payload.get("diff")
            decision = evaluate_breaking_publish(kind, diff=diff)
            if not decision.allowed:
                results.append(
                    {"queue_id": row.queue_id, "status": "skipped", "reason": decision.reason}
                )
                continue

            if review_full and _row_needs_review(row):
                results.append(
                    {"queue_id": row.queue_id, "status": "skipped", "reason": "review_queue_full"}
                )
                continue

            assessment = assess_breaking_credibility(
                page_text=str(row.payload.get("page_text", "")),
                source_url=row.scrape_url,
                topic=row.topic,
            )
            if not assessment.credible:
                results.append(
                    {
                        "queue_id": row.queue_id,
                        "status": "skipped",
                        "reason": f"not_credible:{assessment.reason}",
                        "method": assessment.method,
                    }
                )
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
            results.append({"queue_id": row.queue_id, **outcome, "credibility": assessment.method})
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
            # Per-website daily article cap: defer surplus candidates so they
            # leave the pending lane (otherwise the same capped row stays at the
            # head and is re-served as the "top topic" every run).
            if _domain_capped(row):
                mark_queue_status(row.queue_id, "deferred")
                results.append({"queue_id": row.queue_id, "status": "domain_capped"})
                continue

            # Diversity spacing: a domain in its multi-day cooldown must not be
            # composed at all this run — neither published NOR sent to review (a
            # review draft still re-covers the same project). Keep it pending (not
            # deferred) so it retries once the cooldown clears. MUST run BEFORE the
            # review branch, which otherwise composes + continues past this check.
            if _domain_in_cooldown(row):
                results.append({"queue_id": row.queue_id, "status": "domain_cooldown"})
                continue

            # Duplicate cut: the story may have been covered since this row was
            # enqueued — recompute novelty NOW and expire collapsed rows before
            # any compose (review-bound ones included; a review draft still
            # re-covers the same story). Standard tier only: breaking has its
            # own credibility path and must never be suppressed as a repeat.
            if _novelty_collapsed(row):
                mark_queue_status(row.queue_id, "expired")
                results.append({"queue_id": row.queue_id, "status": "novelty_collapsed"})
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
            decision = evaluate_standard_publish(kind, diff=diff)
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
            mark_queue_status(row.queue_id, "expired")
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
                mark_queue_status(row.queue_id, "indexed_only")
                indexed_only += 1
            else:
                mark_queue_status(row.queue_id, "deferred")
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
        if _domain_capped(row) or _domain_in_cooldown(row):
            continue
        outcome = _compose_review_row(row)
        if outcome.get("status") == "review":
            return {"status": "composed", "queue_id": row.queue_id}
    return {"status": "no_candidate"}
