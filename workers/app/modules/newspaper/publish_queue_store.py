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
    # Breaking (scam/incident) bypasses the cap so an alert is never held behind
    # a pending standard update. The pending partition is scanned, which is fine
    # at its intended size (a handful of weekly diffs, not the old homepage flood).
    if str(payload.get("tier", "")) != PublishTier.BREAKING.value:
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
            )
        )
    return order_for_drain(out)


def _queue_domain(row: QueuedPublishRow) -> str:
    """Source key for diversity: the registrable domain (eTLD+1), falling back to service_id. Collapsing subdomains is deliberate — explore.perawallet.app and perawallet.app are one source, so the interleave can't treat the same project's subdomains as distinct sources and let a burst from one entity through. Matches the key the per-domain compose cap/cooldown use, so the two layers agree."""
    from app.modules.crawler.domain_tracker import domain_from_url

    return domain_from_url(row.scrape_url or "") or (row.service_id or "")


def _interleave_by_source(rows: list[QueuedPublishRow]) -> list[QueuedPublishRow]:
    """Round-robin one item per source per round (random source order each round, random within a source), so a burst of same-source candidates can't monopolize the head before other sources are reached. Every row is kept; this only reorders within a single priority tier."""
    buckets: dict[str, list[QueuedPublishRow]] = {}
    order: list[str] = []
    for row in rows:
        dom = _queue_domain(row)
        if dom not in buckets:
            buckets[dom] = []
            order.append(dom)
        buckets[dom].append(row)
    for dom in buckets:
        random.shuffle(buckets[dom])
    result: list[QueuedPublishRow] = []
    while any(buckets[d] for d in order):
        round_order = [d for d in order if buckets[d]]
        random.shuffle(round_order)
        result.extend(buckets[dom].pop(0) for dom in round_order)
    return result


def order_for_drain(rows: list[QueuedPublishRow]) -> list[QueuedPublishRow]:
    """Drain order. Priority (the interest score) strictly dominates: a higher-priority candidate always precedes a lower one. Within one priority tier, sources are interleaved with a random tiebreak so equivalent-score candidates are diverse and no single domain floods the head of the queue."""
    by_priority: dict[int, list[QueuedPublishRow]] = {}
    for row in rows:
        by_priority.setdefault(row.priority, []).append(row)
    ordered: list[QueuedPublishRow] = []
    for prio in sorted(by_priority, reverse=True):
        ordered.extend(_interleave_by_source(by_priority[prio]))
    return ordered


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
