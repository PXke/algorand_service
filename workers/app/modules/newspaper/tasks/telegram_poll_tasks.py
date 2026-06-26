from __future__ import annotations

import uuid

from app.celery_app import celery_app
from app.core.config import TELEGRAM_BOT_TOKEN
from app.modules.chain_tail.registry_cache import clear_registry_cache, load_enabled_services
from app.modules.newspaper.tasks.publish_tasks import run_publish_pipeline
from app.modules.scraper.core.telegram_urls import is_telegram_scrape_url
from app.modules.scraper.crawler_registry import is_crawler_enabled
from app.modules.scraper.crawler_types import CrawlerType


@celery_app.task(name="app.tasks.scrape.poll_telegram_sources")
def poll_telegram_sources() -> dict[str, object]:
    if not is_crawler_enabled(CrawlerType.TELEGRAM):
        return {"status": "skipped", "reason": "crawler_telegram_disabled", "polled": 0}
    if not TELEGRAM_BOT_TOKEN:
        return {"status": "skipped", "reason": "TELEGRAM_BOT_TOKEN unset", "polled": 0}

    clear_registry_cache()
    entries = [
        e
        for e in load_enabled_services()
        if e.scrape_url and is_telegram_scrape_url(e.scrape_url)
    ]
    results: list[dict[str, str]] = []
    for entry in entries:
        trigger_id = f"telegram-poll-{uuid.uuid4().hex[:16]}"
        outcome = run_publish_pipeline(
            service_id=entry.service_id,
            display_name=entry.display_name,
            scrape_url=entry.scrape_url or "",
            match_kind=entry.match_kind,
            match_value=entry.match_value,
            txid=trigger_id,
            round_num=0,
        )
        results.append({"service_id": entry.service_id, **outcome})

    return {"status": "ok", "polled": len(entries), "results": results}
