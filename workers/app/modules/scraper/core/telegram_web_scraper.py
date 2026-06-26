from __future__ import annotations

import hashlib

from bs4 import BeautifulSoup

from app.core.config import TELEGRAM_WEB_PLAYWRIGHT
from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.telegram_urls import resolve_telegram_preview_url
from app.modules.scraper.core.web_fetch import fetch_html, fetch_with_playwright


class TelegramWebScraperError(Exception):
    pass


def parse_telegram_preview_messages(html: str) -> list[str]:
    """Parse public channel preview HTML from https://t.me/s/…"""
    soup = BeautifulSoup(html, "html.parser")
    lines: list[str] = []
    for block in soup.select(".tgme_widget_message_wrap"):
        date_el = block.select_one("time")
        stamp = date_el.get("datetime", "") if date_el else ""
        text_el = block.select_one(".tgme_widget_message_text")
        if not text_el:
            continue
        text = text_el.get_text("\n", strip=True)
        if not text:
            continue
        prefix = f"[{stamp}] " if stamp else ""
        lines.append(f"{prefix}{text}")
    if lines:
        return lines

    # Fallback: any message-like blocks
    for block in soup.select("[class*='tgme_widget_message']"):
        chunk = block.get_text("\n", strip=True)
        if chunk and len(chunk) > 20:
            lines.append(chunk)
    return lines


class TelegramWebScraper(BaseScraper):
    """
    Crawl public Telegram channels via the web preview (t.me/s/username).
    No bot token required; only works for **public** channels with preview enabled.
    """

    def scrape(self, url: str, source_id: str) -> ScrapeResult:
        preview_url = resolve_telegram_preview_url(url)
        if not preview_url:
            msg = f"invalid telegram web scrape_url: {url!r}"
            raise TelegramWebScraperError(msg)

        html, _final_url = fetch_html(preview_url)
        lines = parse_telegram_preview_messages(html)

        if not lines and TELEGRAM_WEB_PLAYWRIGHT:
            _title, body, _final_url = fetch_with_playwright(preview_url)
            lines = [
                line.strip()
                for line in body.splitlines()
                if line.strip() and len(line.strip()) > 15
            ][:80]

        if not lines:
            if "tgme_page" in html and "subscriber" in html.lower():
                msg = "telegram channel has no public preview messages"
                raise TelegramWebScraperError(msg)
            msg = "could not parse telegram web preview (private channel?)"
            raise TelegramWebScraperError(msg)

        text = "\n".join(lines)
        channel = preview_url.rstrip("/").split("/")[-1]
        title = f"Telegram @{channel} (web)"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ScrapeResult(
            source_id=source_id,
            url=url,
            title=title,
            text=text,
            content_hash=content_hash,
        )
