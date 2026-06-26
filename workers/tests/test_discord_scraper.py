from __future__ import annotations

from app.modules.scraper.core.discord_scraper import format_discord_messages
from app.modules.scraper.core.discord_urls import is_discord_scrape_url, parse_discord_channel_id


def test_parse_discord_channel_id() -> None:
    assert parse_discord_channel_id("discord://channel/123456789012345678") == "123456789012345678"
    assert parse_discord_channel_id("discord:987654321098765432") == "987654321098765432"
    assert parse_discord_channel_id("https://example.com") is None


def test_is_discord_scrape_url() -> None:
    assert is_discord_scrape_url("discord://channel/123456789012345678")
    assert not is_discord_scrape_url("https://algorand.org")


def test_format_discord_messages_chronological() -> None:
    messages = [
        {
            "timestamp": "2024-06-01T12:00:00.000000+00:00",
            "content": "newer",
            "author": {"username": "alice"},
        },
        {
            "timestamp": "2024-06-01T11:00:00.000000+00:00",
            "content": "older",
            "author": {"username": "bob"},
        },
    ]
    lines = format_discord_messages(messages)
    assert len(lines) == 2
    assert "older" in lines[0]
    assert "newer" in lines[1]
