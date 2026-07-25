"""Crawler-type inference from URL scheme and env-driven enable flags."""

import pytest

from app.modules.scraper.crawler_registry import crawl_disabled_reason, is_crawler_enabled
from app.modules.scraper.crawler_types import CrawlerType


def test_browser_scheme_needs_spa(monkeypatch: pytest.MonkeyPatch) -> None:
    """A browser:// URL is disabled with "crawler_web_spa_disabled" when the SPA crawler is off."""
    monkeypatch.setenv("CRAWLER_WEB_SPA_ENABLED", "0")
    assert crawl_disabled_reason("browser://https://app.example.com/dashboard") == (
        "crawler_web_spa_disabled"
    )


def test_youtube_env_enable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enables the YouTube crawler when CRAWLER_YOUTUBE_ENABLED is set via env."""
    monkeypatch.setenv("CRAWLER_YOUTUBE_ENABLED", "1")
    assert is_crawler_enabled(CrawlerType.YOUTUBE)
