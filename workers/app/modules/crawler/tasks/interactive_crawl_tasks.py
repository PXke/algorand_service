"""Celery task wrapper for interactive_crawl.py's click-based SPA exploration."""

from __future__ import annotations

import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.crawler.run_interactive_crawl")
def run_interactive_crawl_task(*, entry_url: str, service_id: str) -> dict[str, object]:
    """Dispatched by interactive_crawl.maybe_trigger_interactive_crawl (fire-and-forget, off the page-storage hot path -- launching a real browser for N clicks is too slow to run inline there)."""
    from app.modules.crawler.interactive_crawl import crawl_interactively

    stored = crawl_interactively(entry_url, service_id=service_id)
    return {"entry_url": entry_url, "service_id": service_id, "stored": stored}
