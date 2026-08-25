"""Cassandra-backed publish queue: enqueue, list pending, and resolve outcomes."""

from __future__ import annotations

import contextlib
import json
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.modules.newspaper.publish_policy import PublishKind, PublishTier, PublishTopic


def queue_row_tier(row: QueuedPublishRow) -> PublishTier:
    """Read a queue row's publish tier from its payload, defaulting to standard."""
    raw = str(row.payload.get("tier", PublishTier.STANDARD.value))
    try:
        return PublishTier(raw)
    except ValueError:
        return PublishTier.STANDARD


# Compose outcomes that resolve a queue row (mark it done). Any other status
# (rate_limited, mistral_failed, review_queue_full, domain_capped, error, ...)
# leaves the row pending for a later attempt. Centralised so a new resolving
# status is added in one place — a missing one here is the bug class behind
# "the same topic keeps reappearing".
TERMINAL_OUTCOMES = frozenset(
    {
        "published",
        "review",
        # Auto-approved article stored to pending_feed_queue for the paced
        # backlog release — the queue row is resolved, the article exists.
        "approved_backlog",
        "duplicate",
        "duplicate_review_pending",
        # The writer itself declined to compose (abort_article) — a deliberate
        # judgment, not a transient failure. Retrying next beat would just
        # re-spend tokens re-researching the same dead subject; resolved like
        # "duplicate" so the row doesn't loop. An admin can still trigger a
        # manual recompose to override.
        "aborted_by_writer",
        # run_article_edit's success outcome — MISSING here caused a real
        # runaway loop (2026-07-17): a completed edit never resolved the
        # queue row, so the row stayed "pending" and drain_breaking_publish_queue
        # (fires every ~2 min) redrained and re-edited the same article on
        # every beat — 165 edit calls / 330 versions in under 4 hours on one
        # live article before this was caught and stopped by hand.
        "edited",
        # run_article_edit's failure outcome ({"reason": "update_failed"}) —
        # only reachable when update_article() returns False, which is ONLY
        # a permanent condition (linked article deleted, malformed id, or
        # never actually published) — a real Cassandra write error raises
        # instead of returning False, so retrying here can never help.
        # Same missing-terminal-status shape as "edited" above; fixed
        # alongside it rather than waiting for a second live incident.
        "failed",
    }
)


def is_terminal_outcome(outcome: dict[str, Any]) -> bool:
    """Check whether a compose outcome resolves its queue row rather than leaving it pending."""
    return outcome.get("status") in TERMINAL_OUTCOMES


@dataclass(frozen=True)
class QueuedPublishRow:
    """One row in the publish queue awaiting compose/publish."""

    queue_id: str
    priority: int
    topic: str
    publish_kind: str
    service_id: str
    display_name: str
    scrape_url: str
    payload: dict[str, Any]
    created_at_epoch: int
    human_pick_day: str | None = None


def enqueue_publish(
    *,
    service_id: str,
    display_name: str,
    scrape_url: str,
    publish_kind: PublishKind,
    topic: PublishTopic,
    priority: int,
    dedupe_key: str,
    payload: dict[str, Any],
) -> tuple[str, bool]:
    """Insert queue row. Returns (queue_id, created). Skips if dedupe_key already pending."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import PublishQueueStmts

    session = get_cassandra_session()
    existing = session.execute(PublishQueueStmts.DEDUPE_GET, (dedupe_key,)).one()
    if existing is not None:
        return str(existing.queue_id), False

    # One pending candidate per service: a source that keeps changing must not
    # stack rows — the next re-scrape re-offers the story once this one resolves.
    # The pending partition is scanned, which is fine at its intended size (a
    # handful of weekly diffs, not the old homepage flood). Used to bypass
    # this cap for the BREAKING tier (an alert must never be held behind a
    # pending standard update) -- that tier was removed entirely 2026-08-25
    # (see PublishTier's docstring), so there is no longer a bypass case.
    for row in session.execute(PublishQueueStmts.LIST_PENDING, ("pending", 2000)):
        if row.service_id == service_id:
            return str(row.queue_id), False

    queue_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    status = "pending"
    payload_json = json.dumps(payload, separators=(",", ":"))

    session.execute(
        PublishQueueStmts.INSERT,
        (
            queue_id,
            status,
            priority,
            topic.value,
            publish_kind.value,
            service_id,
            display_name,
            scrape_url,
            dedupe_key,
            payload_json,
            now,
            now,
        ),
    )
    session.execute(
        PublishQueueStmts.INSERT_PENDING,
        (
            status,
            priority,
            now,
            queue_id,
            service_id,
            topic.value,
            publish_kind.value,
        ),
    )
    session.execute(PublishQueueStmts.INSERT_DEDUPE, (dedupe_key, queue_id, now))
    return str(queue_id), True


def list_pending_queue(*, limit: int = 50) -> list[QueuedPublishRow]:
    """Load pending publish-queue rows with their detail payloads, ordered for draining."""
    from app.core.cassandra import execute_parallel_with_args, get_cassandra_session
    from app.core.statements import PublishQueueStmts

    session = get_cassandra_session()
    pending = list(session.execute(PublishQueueStmts.LIST_PENDING, ("pending", limit)))
    # Fan the per-row detail lookups out concurrently instead of one round-trip
    # per pending row; results come back aligned with `pending` (input order).
    details = execute_parallel_with_args(
        PublishQueueStmts.GET_DETAIL, [(row.queue_id,) for row in pending]
    )
    out: list[QueuedPublishRow] = []
    for row, (ok, result) in zip(pending, details, strict=True):
        detail = result.one() if ok else None
        if detail is None:
            continue
        try:
            payload = json.loads(detail.payload or "{}")
        except json.JSONDecodeError:
            payload = {}
        created = detail.created_at or row.created_at
        epoch = int(created.timestamp()) if created else 0
        out.append(
            QueuedPublishRow(
                queue_id=str(row.queue_id),
                priority=int(row.priority),
                topic=row.topic or "",
                publish_kind=row.publish_kind or "",
                service_id=row.service_id or "",
                display_name=detail.display_name or "",
                scrape_url=detail.scrape_url or "",
                payload=payload,
                created_at_epoch=epoch,
                human_pick_day=detail.human_pick_day or None,
            )
        )
    return order_for_drain(out)


def get_queued_row(queue_id: str) -> QueuedPublishRow | None:
    """Load one publish-queue row by id, regardless of status — for admin actions that target a specific row directly (e.g. an immediate recompose) rather than draining in priority order. Callers that require a particular status (e.g. still "pending") must check row status themselves; this is a plain lookup."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import PublishQueueStmts

    try:
        qid = uuid.UUID(str(queue_id))
    except ValueError:
        return None
    session = get_cassandra_session()
    row = session.execute(PublishQueueStmts.GET_FULL, (qid,)).one()
    if row is None:
        return None
    try:
        payload = json.loads(row.payload or "{}")
    except json.JSONDecodeError:
        payload = {}
    created = row.created_at
    epoch = int(created.timestamp()) if created else 0
    return QueuedPublishRow(
        queue_id=str(qid),
        priority=int(row.priority or 0),
        topic=row.topic or "",
        publish_kind=row.publish_kind or "",
        service_id=row.service_id or "",
        display_name=row.display_name or "",
        scrape_url=row.scrape_url or "",
        payload=payload,
        created_at_epoch=epoch,
        human_pick_day=getattr(row, "human_pick_day", None) or None,
    )


def clear_human_pick(queue_id: str) -> None:
    """Clear a row's human_pick_day once its Lane 1 slot has been consumed, so a spent pin can't be reselected."""
    from datetime import UTC, datetime

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import PublishQueueStmts

    try:
        qid = uuid.UUID(str(queue_id))
    except ValueError:
        return
    session = get_cassandra_session()
    session.execute(PublishQueueStmts.CLEAR_HUMAN_PICK, (datetime.now(tz=UTC), qid))


def _queue_domain(row: QueuedPublishRow) -> str:
    """Source key for diversity: the registrable domain (eTLD+1), falling back to service_id. Collapsing subdomains is deliberate — explore.perawallet.app and perawallet.app are one source, so the interleave can't treat the same project's subdomains as distinct sources and let a burst from one entity through. Matches the key the per-domain compose cap/cooldown use, so the two layers agree."""
    from app.modules.crawler.domain_tracker import domain_from_url

    return domain_from_url(row.scrape_url or "") or (row.service_id or "")


def _effective_weight(row: QueuedPublishRow, *, now_epoch: float) -> float:
    """Priority plus a capped age bonus. A row that's waited longer gets a steadily growing boost, so it eventually earns a fair shot against fresher/higher-scoring competitors instead of waiting behind them indefinitely — capped so an old low-value row can catch up, not leapfrog to permanent first place. Floored above zero: a heavily noise-penalized row (priority 0) must still have SOME chance in the weighted draw below, not a guaranteed-zero weight."""
    from app.core.config import DRAIN_AGE_BONUS_MAX, DRAIN_AGE_BONUS_PER_DAY

    age_days = max(0.0, (now_epoch - row.created_at_epoch) / 86400)
    age_bonus = min(DRAIN_AGE_BONUS_MAX, age_days * DRAIN_AGE_BONUS_PER_DAY)
    return max(0.01, row.priority + age_bonus)


def _weighted_shuffle_key(weight: float) -> float:
    """Efraimidis-Spirakis weighted-random-sampling key: sort descending by this for a weighted-random order without replacement. Higher weight skews toward sorting first on average, without guaranteeing the same winner every single call the way a strict sort would."""
    u = min(max(random.random(), 1e-9), 1 - 1e-9)
    return u ** (1.0 / weight)


def _interleave_by_source(rows: list[QueuedPublishRow]) -> list[QueuedPublishRow]:
    """Round-robin one item per DOMAIN per round (not service_id — a service tracked internally as many small service_ids, e.g. one per xGov governance proposal, must still only get one shot per round). Each round, a domain is represented by its own highest-weight remaining row, and rounds are ordered by a weighted-random draw (see _weighted_shuffle_key) so priority still matters on average without strictly determining the order every time. Every row is kept; this only reorders within a single publish_kind."""
    now_epoch = datetime.now(tz=UTC).timestamp()
    buckets: dict[str, list[QueuedPublishRow]] = {}
    for row in rows:
        buckets.setdefault(_queue_domain(row), []).append(row)
    for dom in buckets:
        buckets[dom].sort(key=lambda r: _effective_weight(r, now_epoch=now_epoch), reverse=True)

    result: list[QueuedPublishRow] = []
    while any(buckets.values()):
        round_candidates = [(dom, rows_[0]) for dom, rows_ in buckets.items() if rows_]
        keyed = [
            (dom, row, _weighted_shuffle_key(_effective_weight(row, now_epoch=now_epoch)))
            for dom, row in round_candidates
        ]
        keyed.sort(key=lambda kv: kv[2], reverse=True)
        for dom, row, _key in keyed:
            result.append(row)
            buckets[dom].pop(0)
    return result


def _round_robin_merge(queues: list[list[QueuedPublishRow]]) -> list[QueuedPublishRow]:
    """Flatten several already-ordered lists together, taking one from each in turn."""
    result: list[QueuedPublishRow] = []
    indices = [0] * len(queues)
    while any(indices[i] < len(q) for i, q in enumerate(queues)):
        for i, q in enumerate(queues):
            if indices[i] < len(q):
                result.append(q[indices[i]])
                indices[i] += 1
    return result


def order_for_drain(rows: list[QueuedPublishRow]) -> list[QueuedPublishRow]:
    """Drain order, combining four fairness mechanisms (root-caused 2026-08-06 with real data: 71 pending service_discovery rows, oldest 15 days, none reaching the top 15 by priority; xGov Governance alone holding 6 of the top-15 content_update slots via 6 distinct per-proposal service_ids).

    1. Kind-interleave: service_discovery's own priority ceiling (~100) can
       never outscore a substantial content_update (~400+), so without a
       reserved cadence new-service coverage is structurally unable to
       compete at all — one of every DRAIN_DISCOVERY_INTERLEAVE_EVERY slots
       is reserved for it whenever any are pending.
    2. Domain-level fairness grouping (not service_id) and (3) a weighted-
       random draw within each group instead of a strict sort — see
       _interleave_by_source.
    4. Age bonus baked into the draw weight (_effective_weight) — no row
       waits forever regardless of how the other three mechanisms shake out.
    """
    from app.core.config import DRAIN_DISCOVERY_INTERLEAVE_EVERY

    by_kind: dict[str, list[QueuedPublishRow]] = {}
    for row in rows:
        by_kind.setdefault(row.publish_kind, []).append(row)

    discovery_kind = PublishKind.SERVICE_DISCOVERY.value
    discovery_queue = _interleave_by_source(by_kind.get(discovery_kind, []))
    other_queue = _round_robin_merge(
        [_interleave_by_source(items) for kind, items in by_kind.items() if kind != discovery_kind]
    )
    if not discovery_queue:
        return other_queue

    every = max(1, DRAIN_DISCOVERY_INTERLEAVE_EVERY)
    result: list[QueuedPublishRow] = []
    d_idx = o_idx = slot = 0
    while d_idx < len(discovery_queue) or o_idx < len(other_queue):
        slot += 1
        if slot % every == 0 and d_idx < len(discovery_queue):
            result.append(discovery_queue[d_idx])
            d_idx += 1
        elif o_idx < len(other_queue):
            result.append(other_queue[o_idx])
            o_idx += 1
        else:
            result.append(discovery_queue[d_idx])
            d_idx += 1
    return result


def mark_queue_done(queue_id: str, *, reason: str = "") -> None:
    """Mark a publish-queue row done."""
    mark_queue_status(queue_id, "done", reason=reason)


def mark_queue_status(queue_id: str, status: str, *, reason: str = "") -> None:
    """Move a queue item out of the pending lane into a terminal status: done, deferred, indexed_only, or expired.

    ``reason`` (the gate name or outcome reason that resolved the row)
    persists on the row so the admin queue view can answer "why" — it
    defaults to the status itself so a resolved row is never left with a
    stale reason from an earlier skip.
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import PublishQueueStmts

    session = get_cassandra_session()
    try:
        qid = UUID(queue_id)
    except ValueError:
        return

    row = session.execute(PublishQueueStmts.GET_STATUS_ROW, (qid,)).one()
    if row is None:
        return

    now = datetime.now(tz=UTC)
    session.execute(PublishQueueStmts.UPDATE_STATUS, (status, reason or status, now, qid))
    if row.status == "pending" and row.created_at is not None:
        session.execute(
            PublishQueueStmts.DELETE_PENDING,
            ("pending", row.priority, row.created_at, qid),
        )
    if row.dedupe_key:
        session.execute(PublishQueueStmts.DELETE_DEDUPE, (row.dedupe_key,))


def record_queue_reason(queue_id: str, reason: str) -> None:
    """Persist why a row was skipped THIS run while it stays pending (cooldown, review slot full, not credible, ...) — the status doesn't change, so this is the only trace the decision leaves. Best-effort: a miss here only loses observability, never correctness."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import PublishQueueStmts

    try:
        qid = UUID(queue_id)
    except ValueError:
        return
    with contextlib.suppress(Exception):
        get_cassandra_session().execute(
            PublishQueueStmts.UPDATE_REASON, (reason, datetime.now(tz=UTC), qid)
        )
