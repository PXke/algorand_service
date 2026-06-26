from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse


def _normalize_url(url: str) -> str:
    raw = url.strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    return raw.rstrip("/")


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
        pass


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

    if not URL_QUEUE_ENABLED:
        return "", False

    normalized = _normalize_url(url)
    if not normalized:
        return "", False

    # Per-URL recrawl cooldown: don't re-queue a link fetched within the window.
    if recently_crawled(normalized):
        return "", False

    session = get_cassandra_session()
    existing = session.execute(
        "SELECT queue_id, status FROM url_queue_by_url WHERE url = %s",
        (normalized,),
    ).one()
    if existing is not None:
        row = session.execute(
            "SELECT status FROM url_queue WHERE queue_id = %s",
            (existing.queue_id,),
        ).one()
        if row and str(row.status) == "pending":
            return str(existing.queue_id), False

    queue_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    status = "pending"
    meta = dict(metadata or {})

    session.execute(
        """
        INSERT INTO url_queue (
          queue_id, url, source, priority, enqueued_at, status, metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (queue_id, normalized, source, priority, now, status, meta),
    )
    session.execute(
        """
        INSERT INTO url_queue_by_url (url, queue_id, enqueued_at)
        VALUES (%s, %s, %s)
        """,
        (normalized, queue_id, now),
    )
    session.execute(
        """
        INSERT INTO url_queue_pending (
          status, priority, enqueued_at, queue_id, url, source
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (status, priority, now, queue_id, normalized, source),
    )
    return str(queue_id), True


def dequeue_url() -> dict[str, Any] | None:
    """Pop highest-priority pending URL and mark it processing."""
    from app.core.cassandra import get_cassandra_session
    from app.core.config import URL_QUEUE_ENABLED

    if not URL_QUEUE_ENABLED:
        return None

    session = get_cassandra_session()
    row = session.execute(
        """
        SELECT queue_id, url, source, priority, enqueued_at
        FROM url_queue_pending
        WHERE status = %s
        LIMIT 1
        """,
        ("pending",),
    ).one()
    if row is None:
        return None

    queue_id = row.queue_id
    session.execute(
        "UPDATE url_queue SET status = %s WHERE queue_id = %s",
        ("processing", queue_id),
    )
    session.execute(
        """
        DELETE FROM url_queue_pending
        WHERE status = %s AND priority = %s AND enqueued_at = %s AND queue_id = %s
        """,
        ("pending", row.priority, row.enqueued_at, queue_id),
    )
    detail = session.execute(
        "SELECT metadata FROM url_queue WHERE queue_id = %s",
        (queue_id,),
    ).one()
    meta = dict(detail.metadata or {}) if detail is not None else {}
    return {
        "queue_id": str(queue_id),
        "url": row.url,
        "source": row.source,
        "priority": int(row.priority),
        "metadata": meta,
    }


def mark_url_done(queue_id: str, *, status: str = "done") -> None:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    try:
        qid = uuid.UUID(queue_id)
    except ValueError:
        return
    session.execute(
        "UPDATE url_queue SET status = %s WHERE queue_id = %s",
        (status, qid),
    )


def pending_url_count() -> int:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    rows = session.execute(
        "SELECT queue_id FROM url_queue_pending WHERE status = %s LIMIT 10000",
        ("pending",),
    )
    return sum(1 for _ in rows)
