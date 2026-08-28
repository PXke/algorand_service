"""URL normalization and the Cassandra-backed crawl frontier queue."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from app.core.redis_client import get_redis

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)


def _client() -> redis.Redis:
    return get_redis()


# Redis hash tracking rows currently in status='processing': field=queue_id,
# value=JSON {url, source, priority, started_at}. url_queue itself has no
# per-row "started processing" timestamp column (enqueued_at is stamped once,
# at enqueue time, and never updated on dequeue) and adding one is a schema
# change; this hash is what reclaim_stale_processing_urls() below reads to
# find a row a worker died on mid-fetch (hard time_limit SIGKILL, a deploy's
# cold-shutdown SIGQUIT, an orphaned process) and never called mark_url_done
# for -- dequeue_url() hands a row to exactly one worker and nothing else
# ever resets a stuck one.
_PROCESSING_REDIS_KEY = "crawl:url_queue:processing"

# How long a row may sit in status='processing' with a live marker before
# reclaim_stale_processing_urls() resets it back to pending. 30 minutes is
# generous above any single drain_url_queue item's real fetch time (including
# the Playwright fallback), so a healthy in-flight fetch is never reclaimed
# out from under its own worker.
STALE_PROCESSING_SECONDS = 1800


def _mark_processing_started(queue_id: str, url: str, source: str, priority: int) -> None:
    """Best-effort Redis record of when a dequeued row started processing, read back by reclaim_stale_processing_urls() below. Fail-open like every other Redis touch in this module (see recently_crawled/mark_url_crawled) -- a write failure here just means this one row won't self-heal if its worker dies, it never blocks or fails the dequeue itself."""
    try:
        payload = json.dumps(
            {
                "url": url,
                "source": source,
                "priority": priority,
                "started_at": datetime.now(tz=UTC).timestamp(),
            }
        )
        _client().hset(_PROCESSING_REDIS_KEY, queue_id, payload)
    except Exception:
        logger.warning(
            "failed to record processing-start marker for queue_id %s", queue_id, exc_info=True
        )


def _clear_processing_started(queue_id: str) -> None:
    """Best-effort removal of a row's processing-start marker once it is no longer in flight (mark_url_done ran, or reclaim_stale_processing_urls just reset it)."""
    try:
        _client().hdel(_PROCESSING_REDIS_KEY, queue_id)
    except Exception:
        logger.warning(
            "failed to clear processing-start marker for queue_id %s", queue_id, exc_info=True
        )


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
        return bool(_client().exists(_cooldown_key(normalized)))
    except Exception:
        return False


def mark_url_crawled(url: str) -> None:
    """Stamp this link as crawled so it is not re-fetched until the cooldown lapses."""
    normalized = _normalize_url(url)
    if not normalized:
        return
    try:
        from app.core.config import CRAWL_URL_RECRAWL_COOLDOWN_SECONDS

        _client().set(_cooldown_key(normalized), "1", ex=CRAWL_URL_RECRAWL_COOLDOWN_SECONDS)
    except Exception:
        logger.warning("failed to mark url crawled: %s", normalized, exc_info=True)


def _row_ttl_seconds() -> int:
    """Configured url_queue row TTL, bound to every queue write (0 = no TTL).

    Cassandra treats `USING TTL 0` as "insert/update without expiring" — the
    documented no-TTL value — so the disabled default binds 0 rather than
    needing a second TTL-less statement shape. Read via the config module (not
    a from-import) at call time so tests and live env flips see the current
    value.
    """
    from app.core import config

    return max(0, config.URL_QUEUE_ROW_TTL_SECONDS)


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

    ttl = _row_ttl_seconds()
    session.execute(
        UrlQueueStmts.INSERT,
        (queue_id, normalized, source, priority, now, status, meta, ttl),
    )
    session.execute(UrlQueueStmts.INSERT_BY_URL, (normalized, queue_id, now, ttl))
    session.execute(
        UrlQueueStmts.INSERT_PENDING,
        (status, priority, now, queue_id, normalized, source, ttl),
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
    session.execute(UrlQueueStmts.UPDATE_STATUS, (_row_ttl_seconds(), "processing", queue_id))
    session.execute(
        UrlQueueStmts.DELETE_PENDING,
        ("pending", row.priority, row.enqueued_at, queue_id),
    )
    _mark_processing_started(str(queue_id), row.url, row.source, int(row.priority))
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
    # Same TTL as the insert path: TTLs are per-cell, so a TTL-less status
    # update would leave a cell that outlives the row's other cells.
    session.execute(UrlQueueStmts.UPDATE_STATUS, (_row_ttl_seconds(), status, qid))
    _clear_processing_started(queue_id)


def reclaim_stale_processing_urls(
    *, max_age_seconds: int = STALE_PROCESSING_SECONDS
) -> dict[str, object]:
    """Beat maintenance: reset any url_queue row whose processing-start marker (see _mark_processing_started) is older than max_age_seconds back to status='pending' and re-insert it into url_queue_pending, so a row a worker died on mid-fetch re-enters the frontier instead of being stuck in status='processing' forever.

    Tracked in Redis, not a new Cassandra column/table (see
    _PROCESSING_REDIS_KEY's module-level comment for why). Best-effort/
    fail-open like every other Redis touch in this module: a Redis hiccup
    here just means a stuck row waits for the next beat tick instead of
    crashing this one, and a row whose marker was never written (e.g. the
    worker crashed between UPDATE_STATUS and the Redis write in
    dequeue_url()) stays stuck until an operator notices -- a known gap of
    this best-effort design, not a silent data-loss path (the row itself is
    untouched, just not self-healing).

    Re-checks each candidate's live Cassandra status before touching it, so
    a row that already finished normally (mark_url_done ran) while its
    Redis marker lingered -- the HDEL itself failed -- is never resurrected
    back into the pending queue.
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import UrlQueueStmts

    now = datetime.now(tz=UTC).timestamp()
    try:
        markers = _client().hgetall(_PROCESSING_REDIS_KEY)
    except Exception:
        logger.warning("failed to read url-queue processing markers", exc_info=True)
        return {"status": "error", "reason": "redis_unavailable", "reclaimed": 0}

    session = get_cassandra_session()
    reclaimed: list[str] = []
    dropped_corrupt = 0
    for queue_id, payload in markers.items():
        try:
            data = json.loads(payload)
            started_at = float(data["started_at"])
            url = str(data["url"])
            if not url:
                raise ValueError("empty url in processing marker")
        except (TypeError, ValueError, KeyError):
            # Corrupt/unusable marker -- drop it so it doesn't wedge every
            # future sweep, but don't touch the underlying row.
            dropped_corrupt += 1
            _clear_processing_started(queue_id)
            continue
        if now - started_at < max_age_seconds:
            continue

        try:
            qid = uuid.UUID(queue_id)
        except ValueError:
            dropped_corrupt += 1
            _clear_processing_started(queue_id)
            continue

        row = session.execute(UrlQueueStmts.GET_STATUS, (qid,)).one()
        if row is None or str(row.status) != "processing":
            # Already finished (or gone) -- just clear the stale marker.
            _clear_processing_started(queue_id)
            continue

        ttl = _row_ttl_seconds()
        enqueued_at = datetime.now(tz=UTC)
        session.execute(UrlQueueStmts.UPDATE_STATUS, (ttl, "pending", qid))
        session.execute(
            UrlQueueStmts.INSERT_PENDING,
            (
                "pending",
                int(data.get("priority") or 30),
                enqueued_at,
                qid,
                url,
                str(data.get("source") or "reclaim_stale_processing"),
                ttl,
            ),
        )
        _clear_processing_started(queue_id)
        reclaimed.append(queue_id)

    return {
        "status": "ok",
        "reclaimed": len(reclaimed),
        "reclaimed_ids": reclaimed,
        "dropped_corrupt_markers": dropped_corrupt,
    }


def pending_url_count() -> int:
    """Count how many URLs are currently pending in the crawl queue."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import UrlQueueStmts

    session = get_cassandra_session()
    rows = session.execute(UrlQueueStmts.LIST_PENDING_IDS, ("pending",))
    return sum(1 for _ in rows)
