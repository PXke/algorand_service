from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.modules.newspaper.publish_policy import PublishKind, PublishTier, PublishTopic


def queue_row_tier(row: QueuedPublishRow) -> PublishTier:
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
    {"published", "review", "duplicate", "duplicate_review_pending"}
)


def is_terminal_outcome(outcome: dict[str, Any]) -> bool:
    return outcome.get("status") in TERMINAL_OUTCOMES


@dataclass(frozen=True)
class QueuedPublishRow:
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

    session = get_cassandra_session()
    existing = session.execute(
        "SELECT queue_id FROM publish_queue_dedupe WHERE dedupe_key = %s",
        (dedupe_key,),
    ).one()
    if existing is not None:
        return str(existing.queue_id), False

    queue_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    status = "pending"
    payload_json = json.dumps(payload, separators=(",", ":"))

    session.execute(
        """
        INSERT INTO publish_queue (
          queue_id, status, priority, topic, publish_kind,
          service_id, display_name, scrape_url, dedupe_key,
          payload, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
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
        """
        INSERT INTO publish_queue_pending (
          status, priority, created_at, queue_id,
          service_id, topic, publish_kind
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
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
    session.execute(
        """
        INSERT INTO publish_queue_dedupe (dedupe_key, queue_id, created_at)
        VALUES (%s, %s, %s)
        """,
        (dedupe_key, queue_id, now),
    )
    return str(queue_id), True


def list_pending_queue(*, limit: int = 50) -> list[QueuedPublishRow]:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    rows = session.execute(
        """
        SELECT queue_id, priority, topic, publish_kind, service_id,
               created_at
        FROM publish_queue_pending
        WHERE status = %s
        LIMIT %s
        """,
        ("pending", limit),
    )
    out: list[QueuedPublishRow] = []
    for row in rows:
        detail = session.execute(
            """
            SELECT display_name, scrape_url, payload, created_at
            FROM publish_queue
            WHERE queue_id = %s
            """,
            (row.queue_id,),
        ).one()
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
    """Source key for diversity: the registrable domain (eTLD+1), falling back to
    service_id. Collapsing subdomains is deliberate — explore.perawallet.app and
    perawallet.app are one source, so the interleave can't treat the same project's
    subdomains as distinct sources and let a burst from one entity through. Matches
    the key the per-domain compose cap/cooldown use, so the two layers agree."""
    from app.modules.crawler.domain_tracker import domain_from_url

    return domain_from_url(row.scrape_url or "") or (row.service_id or "")


def _interleave_by_source(rows: list[QueuedPublishRow]) -> list[QueuedPublishRow]:
    """Round-robin one item per source per round (random source order each
    round, random within a source), so a burst of same-source candidates can't
    monopolize the head before other sources are reached. Every row is kept;
    this only reorders within a single priority tier."""
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
        for dom in round_order:
            result.append(buckets[dom].pop(0))
    return result


def order_for_drain(rows: list[QueuedPublishRow]) -> list[QueuedPublishRow]:
    """Drain order. Priority (the interest score) strictly dominates: a
    higher-priority candidate always precedes a lower one. Within one priority
    tier, sources are interleaved with a random tiebreak so equivalent-score
    candidates are diverse and no single domain floods the head of the queue."""
    by_priority: dict[int, list[QueuedPublishRow]] = {}
    for row in rows:
        by_priority.setdefault(row.priority, []).append(row)
    ordered: list[QueuedPublishRow] = []
    for prio in sorted(by_priority, reverse=True):
        ordered.extend(_interleave_by_source(by_priority[prio]))
    return ordered


def count_pending_queue() -> int:
    """Count pending rows. A single-partition COUNT (status is the partition
    key) — does NOT materialise rows or hit the per-row detail table the way
    ``list_pending_queue`` does, so it stays cheap regardless of queue depth."""
    from app.core.cassandra import get_cassandra_session

    row = get_cassandra_session().execute(
        "SELECT COUNT(*) AS n FROM publish_queue_pending WHERE status = %s",
        ("pending",),
    ).one()
    return int(row.n) if row is not None else 0


def mark_queue_done(queue_id: str) -> None:
    mark_queue_status(queue_id, "done")


def mark_queue_status(queue_id: str, status: str) -> None:
    """
    Move a queue item out of the pending lane into a terminal status:
    done, deferred, indexed_only, or expired.
    """
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    try:
        qid = UUID(queue_id)
    except ValueError:
        return

    row = session.execute(
        """
        SELECT status, priority, created_at, dedupe_key
        FROM publish_queue
        WHERE queue_id = %s
        """,
        (qid,),
    ).one()
    if row is None:
        return

    now = datetime.now(tz=UTC)
    session.execute(
        """
        UPDATE publish_queue
        SET status = %s, updated_at = %s
        WHERE queue_id = %s
        """,
        (status, now, qid),
    )
    if row.status == "pending" and row.created_at is not None:
        session.execute(
            """
            DELETE FROM publish_queue_pending
            WHERE status = %s AND priority = %s AND created_at = %s AND queue_id = %s
            """,
            ("pending", row.priority, row.created_at, qid),
        )
    if row.dedupe_key:
        session.execute(
            "DELETE FROM publish_queue_dedupe WHERE dedupe_key = %s",
            (row.dedupe_key,),
        )
