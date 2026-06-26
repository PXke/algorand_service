from __future__ import annotations

from app.celery_app import celery_app
from app.modules.metrics.price_metrics_service import run_collect_and_prepare_price_metrics
from app.modules.scraper.crawler_registry import metrics_crawl_disabled_reason


@celery_app.task(name="app.tasks.metrics.collect_price_metrics")
def collect_price_metrics() -> dict[str, str]:
    """Periodic CoinGecko poll + Cassandra store + Mistral context brief."""
    off = metrics_crawl_disabled_reason()
    if off:
        return {"status": "skipped", "reason": off}
    return run_collect_and_prepare_price_metrics()
