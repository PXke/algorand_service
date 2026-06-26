from __future__ import annotations

from app.modules.scraper.crawler_types import CrawlerType


class ChainCrawlerDriver:
    """
    On-chain lane: does not fetch HTML — chain tail matches txs and may trigger
    web/reddit/… scrape via registry scrape_url.
    """

    crawler_type = CrawlerType.CHAIN.value
