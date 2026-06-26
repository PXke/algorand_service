from __future__ import annotations

from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.reddit_scraper import RedditScraper
from app.modules.scraper.crawler_types import CrawlerType


class RedditCrawlerDriver:
    crawler_type = CrawlerType.REDDIT.value

    def get_scraper(self, scrape_url: str) -> BaseScraper:
        return RedditScraper()

    def scrape(self, scrape_url: str, source_id: str) -> ScrapeResult:
        result = self.get_scraper(scrape_url).scrape(scrape_url, source_id)
        from app.modules.scraper.core.link_extractor import enqueue_external_links

        enqueue_external_links(result.text, source="reddit", priority=30)
        return result
