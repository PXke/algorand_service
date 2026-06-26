from __future__ import annotations

import hashlib
import re

from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.discord_urls import resolve_discord_web_url
from app.modules.scraper.core.web_fetch import fetch_html, fetch_with_playwright, html_to_plain_text


class DiscordWebScraperError(Exception):
    pass


_LOGIN_MARKERS = (
    "log in to discord",
    "login to discord",
    "you must log in",
    "continue in browser",
)


def _looks_like_login_wall(text: str, title: str) -> bool:
    blob = f"{title}\n{text}".lower()
    return any(marker in blob for marker in _LOGIN_MARKERS)


def _extract_discord_messages(text: str) -> list[str]:
    """Best-effort lines from rendered Discord channel page."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) < 4:
            continue
        if line.lower() in {"discord", "home", "download", "log in"}:
            continue
        lines.append(line)
    # Drop duplicate consecutive
    deduped: list[str] = []
    prev = ""
    for line in lines:
        if line != prev:
            deduped.append(line)
        prev = line
    return deduped[-80:]


class DiscordWebScraper(BaseScraper):
    """
    Crawl Discord like a website: load discord.com channel URLs in a headless browser.
    Public read-only access to official servers usually requires login — expect
    `login_required` unless the page is genuinely public.
    """

    def scrape(self, url: str, source_id: str) -> ScrapeResult:
        web_url = resolve_discord_web_url(url)
        if not web_url:
            msg = (
                f"invalid discord web scrape_url: {url!r} — use "
                "discord://channels/GUILD_ID/CHANNEL_ID or discord://web/https://…"
            )
            raise DiscordWebScraperError(msg)

        try:
            title, body, _final_url = fetch_with_playwright(web_url)
        except Exception as exc:
            # Fallback static fetch (invite pages, marketing)
            try:
                html, _final_url = fetch_html(web_url)
                title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
                title = title_match.group(1).strip() if title_match else "Discord"
                body = html_to_plain_text(html)
            except Exception as fallback_exc:
                msg = f"discord web fetch failed: {exc}; fallback: {fallback_exc}"
                raise DiscordWebScraperError(msg) from exc

        if _looks_like_login_wall(body, title):
            msg = (
                "discord web page requires login — cannot read official channel without "
                "session. Use push ingest or mail instead."
            )
            raise DiscordWebScraperError(msg)

        lines = _extract_discord_messages(body)
        if len(lines) < 3:
            msg = "discord web page had insufficient visible text (SPA/login wall?)"
            raise DiscordWebScraperError(msg)

        text = "\n".join(lines)
        page_title = title.strip() or "Discord channel"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ScrapeResult(
            source_id=source_id,
            url=url,
            title=page_title,
            text=text,
            content_hash=content_hash,
        )
