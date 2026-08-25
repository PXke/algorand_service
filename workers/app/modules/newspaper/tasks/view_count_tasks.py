"""Celery task that flushes Redis's pending view-count increments into Cassandra."""

from __future__ import annotations

from app.celery_app import celery_app
from app.modules.newspaper.view_counts import flush_pending_views


@celery_app.task(name="app.tasks.newspaper.flush_pending_views")
def flush_pending_views_task() -> dict[str, int]:
    """Beat-triggered every 10 minutes: drain Redis's pending per-article view increments into article_view_counts. See view_counts.flush_pending_views for the full rationale."""
    return flush_pending_views()
