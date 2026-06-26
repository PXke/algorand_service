from __future__ import annotations

from app.core import config
from app.core.config import TELEGRAM_BOT_TOKEN
from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.telegram_scraper import TelegramScraper
from app.modules.scraper.core.telegram_web_scraper import TelegramWebScraper
from app.modules.scraper.crawler_types import CrawlerType


class TelegramCrawlerDriver:
    crawler_type = CrawlerType.TELEGRAM.value

    def get_scraper(self, scrape_url: str) -> BaseScraper:
        if config.TELEGRAM_SCRAPE_MODE == "bot" and TELEGRAM_BOT_TOKEN:
            return TelegramScraper()
        return TelegramWebScraper()

    def scrape(self, scrape_url: str, source_id: str) -> ScrapeResult:
        result = self.get_scraper(scrape_url).scrape(scrape_url, source_id)
        from app.modules.scraper.core.link_extractor import enqueue_external_links

        enqueue_external_links(result.text, source="telegram", priority=30)
        return result
