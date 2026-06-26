from __future__ import annotations

from app.modules.scraper.core.base import BaseScraper
from app.modules.scraper.crawler_registry import crawl_disabled_reason, infer_crawler_type
from app.modules.scraper.crawler_types import CrawlerType
from app.modules.scraper.crawlers.discord_crawler import DiscordCrawlerDriver
from app.modules.scraper.crawlers.reddit_crawler import RedditCrawlerDriver
from app.modules.scraper.crawlers.telegram_crawler import TelegramCrawlerDriver
from app.modules.scraper.crawlers.web_crawler import WebCrawlerDriver
from app.modules.scraper.crawlers.youtube_crawler import YoutubeCrawlerDriver


class CrawlerDisabledError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


_DRIVERS = {
    CrawlerType.WEB: WebCrawlerDriver(),
    CrawlerType.REDDIT: RedditCrawlerDriver(),
    CrawlerType.TELEGRAM: TelegramCrawlerDriver(),
    CrawlerType.DISCORD: DiscordCrawlerDriver(),
    CrawlerType.YOUTUBE: YoutubeCrawlerDriver(),
}


def get_scraper_for_url(scrape_url: str) -> BaseScraper:
    reason = crawl_disabled_reason(scrape_url)
    if reason:
        raise CrawlerDisabledError(reason)
    ctype = infer_crawler_type(scrape_url)
    driver = _DRIVERS[ctype]
    return driver.get_scraper(scrape_url)
