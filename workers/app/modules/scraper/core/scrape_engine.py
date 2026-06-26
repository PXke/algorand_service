from __future__ import annotations

from urllib.parse import urlparse

from app.core import config
from app.modules.scraper.core.discord_urls import is_discord_scrape_url, resolve_discord_web_url
from app.modules.scraper.core.telegram_urls import (
    is_telegram_scrape_url,
    resolve_telegram_preview_url,
)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _allowed_browser_domains() -> set[str]:
    raw = config.BROWSER_SCRAPE_DOMAINS.strip().lower()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def uses_browser_engine(scrape_url: str) -> bool:
    """
    Whether this source should use Playwright (hard targets / SPAs).
    """
    if config.SCRAPE_ENGINE_DEFAULT == "http":
        return scrape_url.startswith("browser://")
    if config.SCRAPE_ENGINE_DEFAULT == "browser":
        if scrape_url.startswith("http") or scrape_url.startswith("browser://"):
            return True
        return is_discord_scrape_url(scrape_url) or is_telegram_scrape_url(scrape_url)

    # auto
    if scrape_url.startswith("browser://"):
        return True

    # discord:// and telegram:// use dedicated scrapers (DiscordWebScraper, TelegramWebScraper).
    if is_discord_scrape_url(scrape_url) or is_telegram_scrape_url(scrape_url):
        return False

    resolved = resolve_discord_web_url(scrape_url) or resolve_telegram_preview_url(scrape_url)
    if resolved:
        host = _domain(resolved)
        if host in _allowed_browser_domains():
            return True

    if scrape_url.startswith("http"):
        host = _domain(scrape_url)
        return host in _allowed_browser_domains()

    return False
