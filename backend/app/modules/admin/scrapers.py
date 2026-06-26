"""Admin-triggered worker tasks (run-now buttons in the admin System tab).

The backend only enqueues by task name over the shared broker; the task code
itself lives in workers/. Keep this whitelist in sync with workers/app/celery_app.py
task names and routes.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class ScraperAction:
    action: str
    task: str
    queue: str
    label: str
    description: str


SCRAPER_ACTIONS: dict[str, ScraperAction] = {
    a.action: a
    for a in (
        ScraperAction(
            "reddit_poll",
            "app.tasks.scrape.poll_reddit_sources",
            "scrape",
            "Poll Reddit",
            "Fetch new posts from reddit:// sources in the registry.",
        ),
        ScraperAction(
            "web_diff",
            "app.tasks.newspaper.check_and_publish_mistral_on_diff",
            "pipeline",
            "Scrape web sources",
            "Snapshot web sources, detect diffs and queue articles.",
        ),
        ScraperAction(
            "drain_url_queue",
            "app.tasks.crawler.drain_url_queue",
            "scrape",
            "Drain URL queue",
            "Crawl URLs waiting in the discovery queue.",
        ),
        ScraperAction(
            "drain_ingest",
            "app.tasks.ingest.drain_external_ingest_queue",
            "pipeline",
            "Drain ingest queue",
            "Process externally pushed signals.",
        ),
        ScraperAction(
            "publish_standard",
            "app.tasks.newspaper.drain_standard_publish_queue",
            "pipeline",
            "Publish standard queue",
            "Release queued standard articles (respects daily caps).",
        ),
        ScraperAction(
            "publish_breaking",
            "app.tasks.newspaper.drain_breaking_publish_queue",
            "pipeline",
            "Publish breaking queue",
            "Release queued breaking articles.",
        ),
        ScraperAction(
            "reindex_search",
            "app.tasks.search.reindex_articles",
            "pipeline",
            "Reindex search",
            "Rebuild the Typesense article index.",
        ),
        ScraperAction(
            "collect_metrics",
            "app.tasks.metrics.collect_price_metrics",
            "pipeline",
            "Collect price metrics",
            "Fetch the current ALGO price point.",
        ),
    )
}

_celery = None


def _get_celery():
    global _celery
    if _celery is None:
        from celery import Celery

        _celery = Celery(broker=settings.celery_broker_url)
    return _celery


def trigger_scraper(action: str) -> str:
    """Enqueue the whitelisted action; returns the Celery task id."""
    entry = SCRAPER_ACTIONS[action]
    result = _get_celery().send_task(entry.task, queue=entry.queue)
    return str(result.id)


def celery_overview() -> dict:
    """Ping workers over the broker; report liveness and active task counts."""
    app = _get_celery()
    inspector = app.control.inspect(timeout=1.5)
    ping = inspector.ping() or {}
    active = inspector.active() or {}
    workers = [
        {
            "name": name,
            "online": (reply or {}).get("ok") == "pong",
            "active_tasks": len(active.get(name) or []),
        }
        for name, reply in sorted(ping.items())
    ]
    return {"workers": workers}
