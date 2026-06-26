from __future__ import annotations

from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.youtube_scraper import YoutubeScraper
from app.modules.scraper.crawler_types import CrawlerType


class YoutubeCrawlerDriver:
    crawler_type = CrawlerType.YOUTUBE.value

    def get_scraper(self, scrape_url: str) -> BaseScraper:
        return YoutubeScraper()

    def scrape(self, scrape_url: str, source_id: str) -> ScrapeResult:
        return self.get_scraper(scrape_url).scrape(scrape_url, source_id)
