"""Pick the right scraper for a URL based on the crawler registry."""

from __future__ import annotations

from app.modules.scraper.core.base import BaseScraper
from app.modules.scraper.crawler_registry import crawl_disabled_reason, infer_crawler_type
from app.modules.scraper.crawler_types import CrawlerType
from app.modules.scraper.crawlers.web_crawler import WebCrawlerDriver
from app.modules.scraper.crawlers.youtube_crawler import YoutubeCrawlerDriver


class CrawlerDisabledError(Exception):
    """Raised when no scraper is available because its crawler type is disabled."""

    def __init__(self, reason: str) -> None:
        """Carry the reason this crawler type is disabled."""
        self.reason = reason
        super().__init__(reason)


_DRIVERS = {
    CrawlerType.WEB: WebCrawlerDriver(),
    CrawlerType.YOUTUBE: YoutubeCrawlerDriver(),
}


def get_scraper_for_url(scrape_url: str) -> BaseScraper:
    """Return the driver-appropriate scraper for a URL, raising if its crawler type is disabled."""
    reason = crawl_disabled_reason(scrape_url)
    if reason:
        raise CrawlerDisabledError(reason)
    ctype = infer_crawler_type(scrape_url)
    driver = _DRIVERS[ctype]
    return driver.get_scraper(scrape_url)
