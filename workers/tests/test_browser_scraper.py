"""Browser-backed scrape happy path and unresolved-URL handling."""

from unittest.mock import patch

import pytest

from app.modules.scraper.core.browser_scrape import BrowserPageResult, BrowserScrapeError
from app.modules.scraper.core.browser_scraper import BrowserScraper


def test_browser_scraper_happy_path() -> None:
    """Scrapes a resolved browser:// URL and returns its rendered title, text, and content hash."""
    page = BrowserPageResult(
        title="Roadmap",
        text=(
            "Line one\nLine two with enough content to pass minimum length "
            "checks for browser scrape."
        ),
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


def test_browser_scraper_unresolved_url() -> None:
    """Raises BrowserScrapeError for a URL that cannot be resolved to a real target."""
    with pytest.raises(BrowserScrapeError, match="cannot resolve"):
        BrowserScraper().scrape("not-a-url", "svc-1")


def test_browser_scraper_forwards_playwright_session_to_fetch_page() -> None:
    """A caller-supplied playwright_session must reach fetch_page unchanged, so a batch of BrowserScraper().scrape() calls can share one browser instead of each paying for its own launch."""
    page = BrowserPageResult(
        title="Roadmap",
        text="Line one\nLine two with enough content to pass the minimum length check.",
        final_url="https://app.example.com/roadmap",
        engine="playwright-session",
    )
    sentinel_session = object()
    seen: dict[str, object] = {}

    def _fake_fetch_page(url: str, **kwargs: object) -> BrowserPageResult:
        seen["url"] = url
        seen["playwright_session"] = kwargs.get("playwright_session")
        return page

    with patch(
        "app.modules.scraper.core.browser_scraper.fetch_page",
        side_effect=_fake_fetch_page,
    ):
        BrowserScraper().scrape(
            "browser://https://app.example.com/roadmap",
            "svc-1",
            playwright_session=sentinel_session,
        )
    assert seen["playwright_session"] is sentinel_session
