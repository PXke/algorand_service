"""Crawler driver interface implemented per source type."""

from __future__ import annotations

from typing import Protocol

from app.modules.scraper.core.base import BaseScraper, ScrapeResult


class CrawlerDriver(Protocol):
    """Driver interface implemented per source type."""
    crawler_type: str

    def get_scraper(self, scrape_url: str) -> BaseScraper:
        """Return the scraper to use for a given URL."""
        ...

    def scrape(self, scrape_url: str, source_id: str) -> ScrapeResult:
        """Scrape one URL with this driver's scraper."""
        ...
