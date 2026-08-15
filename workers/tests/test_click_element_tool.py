"""click_element clicks a specific page control by visible text and returns post-click content — for content only reachable via a JS action, not a real href."""

from __future__ import annotations

from typing import Never

import pytest

from app.modules.ai.research_tools import _tool_click_element
from app.modules.scraper.core.browser_scrape import BrowserPageResult, BrowserScrapeError


def test_click_element_requires_url() -> None:
    """An empty url is a usage error, not a fetch attempt."""
    result = _tool_click_element("", "About")
    assert "error" in result


def test_click_element_requires_click_text() -> None:
    """An empty click_text is a usage error, not a fetch attempt."""
    result = _tool_click_element("https://example.com", "")
    assert "error" in result


def test_click_element_returns_post_click_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: click_and_read succeeds, its result is shaped into the tool's public output."""
    page = BrowserPageResult(
        title="LumiRogue",
        text="About this project\nA solo-developer's project backstory, built for fun.",
        final_url="https://lumirogue.com/",
        engine="playwright-click",
    )
    monkeypatch.setattr(
        "app.modules.scraper.core.browser_scrape.click_and_read",
        lambda url, click_text, **kw: page,  # noqa: ARG005
    )
    result = _tool_click_element("lumirogue.com", "About this project")
    assert result["title"] == "LumiRogue"
    assert result["clicked"] == "About this project"
    assert "solo-developer" in result["text"]
    assert result["url"] == "https://lumirogue.com/"


def test_click_element_prepends_https_when_scheme_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare domain (no scheme) is upgraded to https:// before being passed to click_and_read."""
    seen: dict[str, str] = {}

    def fake_click_and_read(url: str, click_text: str, **kw: object) -> BrowserPageResult:  # noqa: ARG001
        seen["url"] = url
        return BrowserPageResult(
            title="t", text="x" * 100, final_url=url, engine="playwright-click"
        )

    monkeypatch.setattr(
        "app.modules.scraper.core.browser_scrape.click_and_read", fake_click_and_read
    )
    _tool_click_element("lumirogue.com", "About")
    assert seen["url"] == "https://lumirogue.com"


def test_click_element_surfaces_no_match_error_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no element matches, click_and_read's error (listing what WAS clickable) passes through."""

    def raise_not_found(url: str, click_text: str, **kw: object) -> Never:  # noqa: ARG001
        msg = "no element with text matching 'Nonexistent' found -- visible clickable text on the page includes: ['Home', 'About']"
        raise BrowserScrapeError(msg)

    monkeypatch.setattr(
        "app.modules.scraper.core.browser_scrape.click_and_read", raise_not_found
    )
    result = _tool_click_element("https://example.com", "Nonexistent")
    assert "error" in result
    assert "Home" in result["error"]


def test_click_element_tool_registered() -> None:
    """Registers click_element in both the tool schemas and handlers."""
    from app.modules.ai.research_tools import research_tools

    schemas, handlers = research_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "click_element" in names
    assert "click_element" in handlers
