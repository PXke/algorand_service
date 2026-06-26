from __future__ import annotations

from app.modules.scraper.crawler_types import CrawlerType


class MetricsCrawlerDriver:
    """
    Chart/metrics lane (price, TVL, nodes) — separate from editorial news crawlers.
    Wired to Celery collect_price_metrics today; TVL/nodes TBD.
    """

    crawler_type = CrawlerType.METRICS.value
