from __future__ import annotations

import uuid

from app.celery_app import celery_app
from app.core.config import REDDIT_USER_AGENT
from app.modules.chain_tail.registry_cache import clear_registry_cache, load_enabled_services
from app.modules.newspaper.tasks.publish_tasks import run_publish_pipeline
from app.modules.scraper.core.reddit_urls import is_reddit_scrape_url
from app.modules.scraper.crawler_registry import is_crawler_enabled
from app.modules.scraper.crawler_types import CrawlerType


@celery_app.task(name="app.tasks.scrape.poll_reddit_sources")
def poll_reddit_sources() -> dict[str, object]:
    """Periodic crawl of service_registry rows whose scrape_url is reddit://…"""
    if not is_crawler_enabled(CrawlerType.REDDIT):
        return {"status": "skipped", "reason": "crawler_reddit_disabled", "polled": 0}
    if not REDDIT_USER_AGENT:
        return {"status": "skipped", "reason": "REDDIT_USER_AGENT unset", "polled": 0}

    clear_registry_cache()
    entries = [
        e for e in load_enabled_services() if e.scrape_url and is_reddit_scrape_url(e.scrape_url)
    ]

    import random
    import time

    from app.core.config import REDDIT_REQUEST_SPACING_SECONDS

    results: list[dict[str, str]] = []
    for idx, entry in enumerate(entries):
        if idx > 0:
            # Space requests so Reddit does not rate-limit us (gentle, jittered).
            time.sleep(REDDIT_REQUEST_SPACING_SECONDS + random.uniform(0, 3))
        trigger_id = f"reddit-poll-{uuid.uuid4().hex[:16]}"
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
