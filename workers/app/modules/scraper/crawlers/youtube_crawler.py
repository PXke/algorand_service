"""CrawlerDriver implementation for YouTube channel sources."""

from __future__ import annotations

from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.youtube_scraper import YoutubeScraper
from app.modules.scraper.crawler_types import CrawlerType


class YoutubeCrawlerDriver:
    """CrawlerDriver implementation for YouTube channel sources."""

    crawler_type = CrawlerType.YOUTUBE.value

    def get_scraper(self, _scrape_url: str) -> BaseScraper:
        """Return the YouTube scraper (ignores the URL; always the same scraper)."""
        return YoutubeScraper()

    def scrape(self, scrape_url: str, source_id: str) -> ScrapeResult:
        """Scrape a channel's recent uploads via the YouTube scraper."""
        return self.get_scraper(scrape_url).scrape(scrape_url, source_id)
