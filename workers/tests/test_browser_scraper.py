from unittest.mock import patch

import pytest

from app.modules.scraper.core.browser_scrape import BrowserPageResult, BrowserScrapeError
from app.modules.scraper.core.browser_scraper import BrowserScraper


def test_browser_scraper_happy_path():
    page = BrowserPageResult(
        title="Roadmap",
        text="Line one\nLine two with enough content to pass minimum length checks for browser scrape.",
        final_url="https://app.example.com/roadmap",
        engine="playwright",
    )
    with patch(
        "app.modules.scraper.core.browser_scraper.fetch_page",
        return_value=page,
    ):
        result = BrowserScraper().scrape(
            "browser://https://app.example.com/roadmap",
            "svc-1",
        )
    assert result.title == "Roadmap"
    assert "Line one" in result.text
    assert result.content_hash


def test_browser_scraper_unresolved_url():
    with pytest.raises(BrowserScrapeError, match="cannot resolve"):
        BrowserScraper().scrape("not-a-url", "svc-1")
