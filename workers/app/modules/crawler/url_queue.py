"""URL normalization and the Cassandra-backed crawl frontier queue."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _normalize_url(url: str) -> str:
    """Cooldown-key form: strips a leading "www." the same way search/classifier/score.py's _hostname() already does for domain identity, so https://example.com and https://www.example.com collapse to the same key. Without this, whichever discovery path happens to enqueue the other variant (they're different netlocs, not different pages) bypasses the per-URL cooldown entirely — the same site gets hit twice within minutes instead of once per window (root-caused 2026-07-21: quantoz.com fetched twice five minutes apart via www./bare variants)."""
    raw = url.strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    normalized = parsed._replace(netloc=netloc)
    return normalized.geturl().rstrip("/")


def _cooldown_key(normalized_url: str) -> str:
    return f"crawl:url:{normalized_url}"


def recently_crawled(url: str) -> bool:
    """Whether this exact link was crawled within the recrawl-cooldown window."""
    normalized = _normalize_url(url)
    if not normalized:
        return False
    try:
        import redis

        from app.core.config import REDIS_URL

        client = redis.from_url(REDIS_URL, decode_responses=True)
        return bool(client.exists(_cooldown_key(normalized)))
    except Exception:
        return False


def mark_url_crawled(url: str) -> None:
    """Stamp this link as crawled so it is not re-fetched until the cooldown lapses."""
    normalized = _normalize_url(url)
    if not normalized:
        return
    try:
        import redis

        from app.core.config import CRAWL_URL_RECRAWL_COOLDOWN_SECONDS, REDIS_URL

        redis.from_url(REDIS_URL, decode_responses=True).set(
            _cooldown_key(normalized), "1", ex=CRAWL_URL_RECRAWL_COOLDOWN_SECONDS
        )
    except Exception:
        logger.warning("failed to mark url crawled: %s", normalized, exc_info=True)


def enqueue_url(
    url: str,
    *,
    source: str,
    priority: int = 30,
    metadata: dict[str, str] | None = None,
) -> tuple[str, bool]:
    """Enqueue a URL for web crawling. Returns (queue_id, created). Skips duplicate pending URLs."""
    from app.core.cassandra import get_cassandra_session
    from app.core.config import URL_QUEUE_ENABLED
    from app.core.statements import UrlQueueStmts

    if not URL_QUEUE_ENABLED:
        return "", False

    normalized = _normalize_url(url)
    if not normalized:
        return "", False

    # Per-URL recrawl cooldown: don't re-queue a link fetched within the window.
    if recently_crawled(normalized):
        return "", False

    session = get_cassandra_session()
    existing = session.execute(UrlQueueStmts.BY_URL, (normalized,)).one()
    if existing is not None:
        row = session.execute(UrlQueueStmts.GET_STATUS, (existing.queue_id,)).one()
        if row and str(row.status) == "pending":
            return str(existing.queue_id), False

    queue_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    status = "pending"
    meta = dict(metadata or {})

    session.execute(
        UrlQueueStmts.INSERT,
        (queue_id, normalized, source, priority, now, status, meta),
    )
    session.execute(UrlQueueStmts.INSERT_BY_URL, (normalized, queue_id, now))
    session.execute(
        UrlQueueStmts.INSERT_PENDING,
        (status, priority, now, queue_id, normalized, source),
    )
    return str(queue_id), True


def dequeue_url() -> dict[str, Any] | None:
    """Pop a pending URL and mark it processing.

    Picks randomly among the top URL_QUEUE_RANDOM_PICK_POOL pending rows
    (by the table's priority/enqueued_at clustering order) rather than always
    the single front row. A large same-priority batch (e.g. one backfill run)
    would otherwise drain in strict insertion order — hammering one domain's
    URLs back-to-back before moving on to the next. Set the pool to 1 to
    restore the old strictly-front-of-queue behavior.
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.config import URL_QUEUE_ENABLED, URL_QUEUE_RANDOM_PICK_POOL
    from app.core.statements import UrlQueueStmts

    if not URL_QUEUE_ENABLED:
        return None

    session = get_cassandra_session()
    pool_size = max(1, URL_QUEUE_RANDOM_PICK_POOL)
    if pool_size == 1:
        row = session.execute(UrlQueueStmts.PEEK_PENDING, ("pending",)).one()
    else:
        import random

        candidates = list(session.execute(UrlQueueStmts.PEEK_PENDING_BATCH, ("pending", pool_size)))
        row = random.choice(candidates) if candidates else None
    if row is None:
        return None

    queue_id = row.queue_id
    session.execute(UrlQueueStmts.UPDATE_STATUS, ("processing", queue_id))
    session.execute(
        UrlQueueStmts.DELETE_PENDING,
        ("pending", row.priority, row.enqueued_at, queue_id),
    )
    detail = session.execute(UrlQueueStmts.GET_METADATA, (queue_id,)).one()
    meta = dict(detail.metadata or {}) if detail is not None else {}
    return {
        "queue_id": str(queue_id),
        "url": row.url,
        "source": row.source,
        "priority": int(row.priority),
        "metadata": meta,
    }


def mark_url_done(queue_id: str, *, status: str = "done") -> None:
    """Update a queued URL's status, silently ignoring a malformed queue_id."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import UrlQueueStmts

    session = get_cassandra_session()
    try:
        qid = uuid.UUID(queue_id)
    except ValueError:
        return
    session.execute(UrlQueueStmts.UPDATE_STATUS, (status, qid))


def pending_url_count() -> int:
    """Count how many URLs are currently pending in the crawl queue."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import UrlQueueStmts

    session = get_cassandra_session()
    rows = session.execute(UrlQueueStmts.LIST_PENDING_IDS, ("pending",))
    return sum(1 for _ in rows)
