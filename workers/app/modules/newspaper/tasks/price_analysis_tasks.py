from __future__ import annotations

from app.celery_app import celery_app
from app.modules.newspaper.weekly_digest_publish import run_weekly_digest_publish


@celery_app.task(name="app.tasks.newspaper.publish_weekly_price_analysis")
def publish_weekly_price_analysis() -> dict[str, str]:
    """Publish weekly digest (market + feed highlights). Celery name kept for compatibility."""
    return run_weekly_digest_publish()


@celery_app.task(name="app.tasks.newspaper.publish_weekly_digest")
def publish_weekly_digest() -> dict[str, str]:
    """Alias for the weekly digest beat task."""
    return run_weekly_digest_publish()
