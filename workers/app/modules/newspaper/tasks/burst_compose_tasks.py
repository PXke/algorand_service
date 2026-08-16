"""One-day-ahead burst compose (2026-08-16): decouple WHEN today's 3 candidates are composed from WHEN they actually go live.

Two tasks, run on separate beat entries:

  - `select_daily_burst`: once per day, picks the human pick (Lane 1, if any
    was set via the existing "Pick for today" action) + the top discovery
    candidate (Lane 3) + the top scale candidate (Lane 2), stamps each
    publish_queue row with today's date in `burst_day`, and records the
    selection in Redis (publish_daily_guard.record_burst_selection) so it's
    both idempotent (won't re-select if it already ran today) and readable
    without a full-table scan.

  - `burst_compose_today`: run later the same day, inside the DeepSeek
    off-peak window. Composes every row `select_daily_burst` picked, reusing
    `compose_queue_row_now` -- the existing admin "compose this one row
    right now" entry point -- so every gate the standard drain already
    enforces (novelty, quality, domain caps, ...) still applies unchanged.
    The resulting article is stamped with the same `burst_day` sentinel
    (`_require_off_peak` inside compose_scrape_article already keeps this
    task itself from spending anything during peak hours even if it's ever
    triggered early -- this is belt-and-suspenders, not the only guard).

Both are ordinary, individually-idempotent tasks -- if a beat tick is
missed (e.g. celery was stopped), the next tick just picks up where the
Redis-recorded state left off, nothing is lost or double-run.

A burst-composed article always lands in the classifier review queue for an
explicit admin decision (see publish_tasks.py's `if row.burst_day:` guard,
which suppresses the autonomous auto-approve bypass) and, once approved,
is force-queued into pending_feed_queue rather than ever publishing
same-day (see the backend admin store's `_publish_or_queue_article`) --
publishing itself keeps its existing paced cadence
(NEWS_STANDARD_INTERVAL_HOURS), just fed from this pre-composed,
pre-approved batch instead of triggering a fresh compose per release.
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.celery_app import celery_app
from app.core import config

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.newspaper.select_daily_burst")
def select_daily_burst() -> dict[str, object]:
    """Pick today's up-to-3 burst candidates (human pick + top discovery + top scale) and mark them, once per day."""
    if config.AUTO_COMPOSE_PAUSED:
        return {"status": "skipped", "reason": "auto_compose_paused"}

    from app.modules.newspaper.publish_daily_guard import (
        _day_key,
        burst_selection_today,
        record_burst_selection,
    )

    already = burst_selection_today()
    if already:
        return {"status": "already_selected", "queue_ids": already}

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import PublishQueueStmts
    from app.modules.newspaper.publish_policy import PublishKind, PublishTier
    from app.modules.newspaper.publish_queue_store import list_pending_queue, queue_row_tier

    today = _day_key()
    pending = [
        row
        for row in list_pending_queue(limit=config.PUBLISH_QUEUE_BATCH_LIMIT)
        if queue_row_tier(row) == PublishTier.STANDARD
    ]

    selected = []
    human = next((row for row in pending if row.human_pick_day == today), None)
    if human is not None:
        selected.append(human)

    discovery = sorted(
        (row for row in pending if row.publish_kind == PublishKind.SERVICE_DISCOVERY.value),
        key=lambda row: -row.priority,
    )
    if discovery:
        selected.append(discovery[0])

    scale = sorted(
        (row for row in pending if row.publish_kind != PublishKind.SERVICE_DISCOVERY.value),
        key=lambda row: -row.priority,
    )
    if scale:
        selected.append(scale[0])

    if not selected:
        return {"status": "nothing_pending"}

    from datetime import UTC, datetime

    session = get_cassandra_session()
    now = datetime.now(tz=UTC)
    queue_ids: list[str] = []
    for row in selected:
        session.execute(PublishQueueStmts.SET_BURST_DAY, (today, now, UUID(row.queue_id)))
        queue_ids.append(row.queue_id)

    record_burst_selection(queue_ids)
    logger.info("select_daily_burst: selected %d candidate(s) for %s", len(queue_ids), today)
    return {"status": "selected", "day": today, "queue_ids": queue_ids, "count": len(queue_ids)}


@celery_app.task(name="app.tasks.newspaper.burst_compose_today")
def burst_compose_today() -> dict[str, object]:
    """Compose every row this day's select_daily_burst picked that hasn't been composed yet."""
    if config.AUTO_COMPOSE_PAUSED:
        return {"status": "skipped", "reason": "auto_compose_paused"}

    from app.modules.newspaper.publish_daily_guard import _day_key, burst_selection_today

    queue_ids = burst_selection_today()
    if not queue_ids:
        return {"status": "nothing_selected"}

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts
    from app.modules.newspaper.tasks.publish_tasks import compose_queue_row_now

    today = _day_key()
    session = get_cassandra_session()
    results = []
    for queue_id in queue_ids:
        outcome = compose_queue_row_now(queue_id)
        article_id = outcome.get("article_id")
        if article_id:
            try:
                session.execute(ArticleStmts.SET_ARTICLE_BURST_DAY, (today, UUID(article_id)))
            except ValueError:
                logger.warning("burst_compose_today: bad article_id %r from %s", article_id, queue_id)
        results.append({"queue_id": queue_id, **outcome})
        logger.info(
            "burst_compose_today: %s -> %s", queue_id, outcome.get("status", "unknown")
        )
    return {"status": "done", "day": today, "results": results}
