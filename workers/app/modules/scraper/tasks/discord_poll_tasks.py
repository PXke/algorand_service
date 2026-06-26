from __future__ import annotations

import uuid

from app.celery_app import celery_app
from app.core.config import DISCORD_BOT_TOKEN
from app.modules.chain_tail.registry_cache import clear_registry_cache, load_enabled_services
from app.modules.newspaper.tasks.publish_tasks import run_publish_pipeline
from app.modules.scraper.core.discord_urls import is_discord_scrape_url
from app.modules.scraper.crawler_registry import is_crawler_enabled
from app.modules.scraper.crawler_types import CrawlerType


@celery_app.task(name="app.tasks.scrape.poll_discord_sources")
def poll_discord_sources() -> dict[str, object]:
    """Periodic crawl of service_registry rows whose scrape_url is discord://…"""
    if not is_crawler_enabled(CrawlerType.DISCORD):
        return {"status": "skipped", "reason": "crawler_discord_disabled", "polled": 0}
    if not DISCORD_BOT_TOKEN:
        return {"status": "skipped", "reason": "DISCORD_BOT_TOKEN unset", "polled": 0}

    clear_registry_cache()
    entries = [
        e for e in load_enabled_services() if e.scrape_url and is_discord_scrape_url(e.scrape_url)
    ]

    results: list[dict[str, str]] = []
    for entry in entries:
        trigger_id = f"discord-poll-{uuid.uuid4().hex[:16]}"
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
