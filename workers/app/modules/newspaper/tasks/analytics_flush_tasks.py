"""Celery task that flushes Redis's pending deferred pageview-analytics deltas into Cassandra."""

from __future__ import annotations

from app.celery_app import celery_app
from app.modules.newspaper.analytics_flush import flush_pending_analytics


@celery_app.task(name="app.tasks.newspaper.flush_pending_analytics")
def flush_pending_analytics_task() -> dict[str, int]:
    """Beat-triggered every ANALYTICS_FLUSH_SECONDS: drain Redis's pending deferred pageview-analytics deltas (geo/campaign/hour/language/referrer_path/referrer_url) into their Cassandra counters. See analytics_flush.flush_pending_analytics for the full rationale."""
    return flush_pending_analytics()
