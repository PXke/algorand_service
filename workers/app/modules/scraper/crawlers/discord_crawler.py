from __future__ import annotations

from app.core import config
from app.core.config import DISCORD_BOT_TOKEN
from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.discord_scraper import DiscordScraper
from app.modules.scraper.core.discord_web_scraper import DiscordWebScraper
from app.modules.scraper.crawler_registry import is_web_spa_enabled
from app.modules.scraper.crawler_types import CrawlerType


class DiscordCrawlerDriver:
    crawler_type = CrawlerType.DISCORD.value

    def get_scraper(self, scrape_url: str) -> BaseScraper:
        if config.DISCORD_SCRAPE_MODE == "bot" and DISCORD_BOT_TOKEN:
            return DiscordScraper()
        if not is_web_spa_enabled():
            msg = "discord web requires web SPA enabled (CRAWLER_WEB_SPA_ENABLED=1)"
            raise DiscordWebDisabledError(msg)
        return DiscordWebScraper()

    def scrape(self, scrape_url: str, source_id: str) -> ScrapeResult:
        result = self.get_scraper(scrape_url).scrape(scrape_url, source_id)
        from app.modules.scraper.core.link_extractor import enqueue_external_links

        enqueue_external_links(result.text, source="discord", priority=30)
        return result


class DiscordWebDisabledError(Exception):
    pass
