from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any


def enqueue_classifier_review(
    *,
    url: str,
    page_text: str,
    page_title: str,
    category: str,
    storage_score: float,
    metadata: dict[str, str] | None = None,
) -> str:
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ClassifierReviewStmts

    review_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    status = "pending"
    meta = json.dumps(metadata or {}, separators=(",", ":"))
    session = get_cassandra_session()
    session.execute(
        ClassifierReviewStmts.INSERT_QUEUE,
        (
            review_id,
            url,
            page_text[:50_000],
            page_title[:512],
            category,
            storage_score,
            status,
            now,
            {"raw": meta},
        ),
    )
    session.execute(
        ClassifierReviewStmts.INSERT_PENDING,
        (status, now, review_id, url, category),
    )
    return str(review_id)


def count_pending_reviews(*, scan_limit: int = 500) -> int:
    """Number of items currently awaiting admin classification."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ClassifierReviewStmts

    rows = get_cassandra_session().execute(
        ClassifierReviewStmts.COUNT_PENDING, ("pending", scan_limit)
    )
    return sum(1 for _ in rows)


def review_queue_full() -> bool:
    """Whether the 1-slot review gate (MAX_PENDING_REVIEWS) is occupied.

    Deliberately checked at MULTIPLE call sites (both drains at run start +
    refreshed after a fill, ensure_review_ready, and inside the compose's
    held-review path) — audited 2026-07-18 and kept: these are the same
    predicate at DIFFERENT decision moments/entry points, not redundant
    computation. The inner publish-path check is the only protection for
    non-drain callers (admin recompose, editorial assignments), and the
    drain-level checks avoid burning a full Mistral compose on a row whose
    review outcome couldn't land anyway. Collapsing them to one site would
    remove protection, not duplication."""
    from app.core.config import MAX_PENDING_REVIEWS

    return count_pending_reviews() >= MAX_PENDING_REVIEWS


def has_pending_review_for_url(url: str, *, scan_limit: int = 500) -> bool:
    """True when a pending review already covers this URL (dedupe guard so a
    fast-changing source doesn't pile up one held article per crawl cycle)."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ClassifierReviewStmts

    session = get_cassandra_session()
    rows = session.execute(
        ClassifierReviewStmts.LIST_PENDING_URLS, ("pending", scan_limit)
    )
    normalized = url.strip().rstrip("/")
    return any((row.url or "").strip().rstrip("/") == normalized for row in rows)


def list_pending_reviews(*, limit: int = 50) -> list[dict[str, Any]]:
    from app.core.cassandra import execute_parallel_with_args, get_cassandra_session
    from app.core.statements import ClassifierReviewStmts

    session = get_cassandra_session()
    pending = list(
        session.execute(ClassifierReviewStmts.LIST_PENDING, ("pending", limit))
    )
    # Fan the per-row detail lookups out concurrently (aligned with `pending`).
    details = execute_parallel_with_args(
        ClassifierReviewStmts.GET_DETAIL, [(row.review_id,) for row in pending]
    )
    items: list[dict[str, Any]] = []
    for _row, (ok, result) in zip(pending, details, strict=True):
        detail = result.one() if ok else None
        if detail is None:
            continue
        article_id = ""
        meta = detail.metadata or {}
        if isinstance(meta, dict):
            raw = meta.get("raw")
            if raw:
                try:
                    parsed = json.loads(raw)
                    article_id = str(parsed.get("article_id", ""))
                except (json.JSONDecodeError, TypeError):
                    article_id = str(meta.get("article_id", ""))
            else:
                article_id = str(meta.get("article_id", ""))
        items.append(
            {
                "review_id": str(detail.review_id),
                "url": detail.url,
                "page_title": detail.page_title or "",
                "page_text_preview": (detail.page_text or "")[:500],
                "category": detail.category or "",
                "storage_score": float(detail.storage_score or 0),
                "article_id": article_id,
            }
        )
    return items


def complete_classifier_review(
    review_id: str,
    *,
    resolution: str = "completed",
) -> bool:
    """Remove from pending index and mark review row resolved."""
    from uuid import UUID

    from app.core.cassandra import get_cassandra_session

    try:
        rid = UUID(review_id)
    except ValueError:
        return False

    from app.core.statements import ClassifierReviewStmts

    session = get_cassandra_session()
    row = session.execute(ClassifierReviewStmts.GET_FULL, (rid,)).one()
    if row is None:
        return False

    created = row.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)

    session.execute(
        ClassifierReviewStmts.INSERT_QUEUE,
        (
            rid,
            row.url,
            row.page_text,
            row.page_title,
            row.category,
            row.storage_score,
            resolution,
            created or datetime.now(tz=UTC),
            dict(row.metadata or {}),
        ),
    )
    if created is not None:
        session.execute(
            ClassifierReviewStmts.DELETE_PENDING,
            ("pending", created, rid),
        )
    return True
