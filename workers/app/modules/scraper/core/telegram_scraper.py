from __future__ import annotations

import hashlib

import httpx

from app.core.config import TELEGRAM_BOT_TOKEN
from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.http_retry import request_with_retry
from app.modules.scraper.core.telegram_urls import parse_telegram_chat_ref


class TelegramScraperError(Exception):
    pass


class TelegramScraper(BaseScraper):
    """
    Minimal Telegram ingest via Bot API getUpdates.
    Production should use channel-specific ingestion; this stub aggregates bot updates.
    """

    def scrape(self, url: str, source_id: str) -> ScrapeResult:
        if not TELEGRAM_BOT_TOKEN:
            msg = "TELEGRAM_BOT_TOKEN is not set"
            raise TelegramScraperError(msg)

        chat_ref = parse_telegram_chat_ref(url) or "default"
        api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        with httpx.Client(timeout=20.0) as client:
            resp = request_with_retry(
                client,
                "GET",
                f"{api}/getUpdates",
                params={"limit": 50},
            )
            if resp.status_code == 429:
                msg = "telegram rate limited"
                raise TelegramScraperError(msg)
            resp.raise_for_status()
            updates = resp.json().get("result") or []

        lines: list[str] = []
        for item in updates:
            msg = item.get("message") or item.get("channel_post") or {}
            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            if chat_ref not in (chat_id, chat.get("username", ""), "default"):
                continue
            text = (msg.get("text") or "").strip()
            if text:
                lines.append(text)

        body = "\n".join(lines) if lines else f"(no recent updates for {chat_ref})"
        title = f"Telegram {chat_ref}"
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return ScrapeResult(
            source_id=source_id,
            url=url,
            title=title,
            text=body,
            content_hash=content_hash,
        )
