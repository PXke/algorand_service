from __future__ import annotations

from typing import Protocol

from app.modules.scraper.core.base import BaseScraper, ScrapeResult


class CrawlerDriver(Protocol):
    crawler_type: str

    def get_scraper(self, scrape_url: str) -> BaseScraper: ...

    def scrape(self, scrape_url: str, source_id: str) -> ScrapeResult: ...
