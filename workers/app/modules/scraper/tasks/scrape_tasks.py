from __future__ import annotations

from app.celery_app import celery_app
from app.core.redis_lock import single_flight
from app.modules.scraper.core.factory import get_scraper_for_url
from app.modules.scraper.core.scrape_cooldown import (
    clear_scrape_cooldown,
    cooldown_for_exception,
    is_on_cooldown,
    record_scrape_failure,
)
from app.modules.scraper.crawler_registry import crawl_disabled_reason


@celery_app.task(name="app.tasks.scrape.fetch_source")
@single_flight(lambda source_id, url: f"scrape:{source_id}", ttl=900)
def fetch_source(source_id: str, url: str) -> dict[str, str]:
    disabled = crawl_disabled_reason(url)
    if disabled:
        return {"status": "skipped", "reason": disabled, "source_id": source_id, "url": url}

    on_cooldown, reason = is_on_cooldown(source_id)
    if on_cooldown:
        return {"status": "skipped", "reason": reason, "source_id": source_id, "url": url}

    scraper = get_scraper_for_url(url)
    try:
        result = scraper.scrape(url=url, source_id=source_id)
    except Exception as exc:
        # Expected for bot-blocked/paywalled/thin pages (403, insufficient text)
        # and dead domains (DNS failure). Back off instead of crashing the task
        # every poll; permanent failures get a long fixed cooldown.
        duration = record_scrape_failure(source_id, seconds=cooldown_for_exception(exc))
        return {
            "status": "error",
            "source_id": source_id,
            "url": url,
            "detail": str(exc),
            "cooldown_seconds": str(duration),
        }
    clear_scrape_cooldown(source_id)
    return {
        "source_id": result.source_id,
        "url": result.url,
        "title": result.title,
        "content_hash": result.content_hash,
        "preview": result.text[:500],
    }
