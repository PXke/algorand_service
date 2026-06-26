from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import httpx

from app.core.config import DISCORD_BOT_TOKEN, DISCORD_MESSAGE_LIMIT
from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.discord_urls import parse_discord_channel_id
from app.modules.scraper.core.http_retry import request_with_retry

DISCORD_API_BASE = "https://discord.com/api/v10"


class DiscordScraperError(Exception):
    pass


class DiscordScraper(BaseScraper):
    """Fetch recent messages from a Discord channel via Bot API (RFC 9110 HTTP)."""

    def __init__(
        self,
        *,
        token: str | None = None,
        message_limit: int | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._token = (token if token is not None else DISCORD_BOT_TOKEN).strip()
        self._message_limit = message_limit if message_limit is not None else DISCORD_MESSAGE_LIMIT
        self._timeout = timeout

    def scrape(self, url: str, source_id: str) -> ScrapeResult:
        if not self._token:
            msg = "DISCORD_BOT_TOKEN is not set"
            raise DiscordScraperError(msg)

        channel_id = parse_discord_channel_id(url)
        if not channel_id:
            msg = f"invalid discord scrape_url: {url!r}"
            raise DiscordScraperError(msg)

        headers = {
            "Authorization": f"Bot {self._token}",
            "User-Agent": "AlgorandPlatformNewspaper/1.0",
        }
        with httpx.Client(timeout=self._timeout, headers=headers) as client:
            channel = request_with_retry(
                client,
                "GET",
                f"{DISCORD_API_BASE}/channels/{channel_id}",
            )
            channel.raise_for_status()
            channel_name = channel.json().get("name") or channel_id

            messages_resp = request_with_retry(
                client,
                "GET",
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                params={"limit": min(max(self._message_limit, 1), 100)},
            )
            messages_resp.raise_for_status()
            messages = messages_resp.json()

        lines = format_discord_messages(messages)
        text = "\n".join(lines)
        title = f"Discord #{channel_name}"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ScrapeResult(
            source_id=source_id,
            url=url,
            title=title,
            text=text,
            content_hash=content_hash,
        )


def format_discord_messages(messages: list[dict]) -> list[str]:
    """Newest-first API order → chronological lines for diffing."""
    rows: list[tuple[datetime, str]] = []
    for msg in reversed(messages):
        content = (msg.get("content") or "").strip()
        embeds = msg.get("embeds") or []
        for embed in embeds:
            part = (embed.get("description") or embed.get("title") or "").strip()
            if part and part not in content:
                content = f"{content}\n{part}".strip() if content else part
        if not content:
            continue
        author = (
            (msg.get("author") or {}).get("global_name")
            or (msg.get("author") or {}).get("username")
            or "unknown"
        )
        ts_raw = msg.get("timestamp") or ""
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            ts = datetime.now(tz=UTC)
        stamp = ts.strftime("%Y-%m-%d %H:%M UTC") if ts_raw else "unknown-time"
        rows.append((ts, f"[{stamp}] {author}: {content}"))

    rows.sort(key=lambda row: row[0])
    return [line for _, line in rows]
