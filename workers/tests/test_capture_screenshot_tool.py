"""capture_screenshot lets the writer illustrate an article with real visual evidence (a game's actual UI, a marketplace listing) instead of describing it in prose alone -- owner request 2026-08-11, after personally buying a Lumi Rogue Ankh and playing the game."""

from __future__ import annotations

import pytest

from app.modules.ai.research_tools import _tool_capture_screenshot


class _FakeSession:
    def __init__(self, png: bytes | None = None, error: Exception | None = None) -> None:
        self._png = png
        self._error = error
        self.calls: list[tuple[str, bool]] = []

    def capture_screenshot(self, url: str, *, full_page: bool = False) -> bytes:
        self.calls.append((url, full_page))
        if self._error:
            raise self._error
        assert self._png is not None
        return self._png


def test_capture_screenshot_requires_url() -> None:
    """An empty url is a usage error, not a capture attempt."""
    result = _tool_capture_screenshot("")
    assert "error" in result


def test_capture_screenshot_requires_a_browser_session() -> None:
    """No playwright_session means no browser is available -- fail clearly rather than silently no-op."""
    result = _tool_capture_screenshot("https://example.com")
    assert "error" in result
    assert "session" in result["error"]


def test_capture_screenshot_returns_image_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: a captured PNG gets saved and its public URL returned."""
    session = _FakeSession(png=b"fake-png-bytes")
    monkeypatch.setattr(
        "app.modules.scraper.core.browser_scrape.save_screenshot",
        lambda png: f"https://algorand.pxke.me/media/screenshots/{len(png)}.png",
    )
    result = _tool_capture_screenshot("lumirogue.com", full_page=True, playwright_session=session)
    assert result["image_url"] == "https://algorand.pxke.me/media/screenshots/14.png"
    assert result["url"] == "https://lumirogue.com"
    assert result["full_page"] is True
    assert session.calls == [("https://lumirogue.com", True)]


def test_capture_screenshot_surfaces_storage_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A captured screenshot that can't be saved (storage unset/failed) is a clear error, not a silent drop."""
    session = _FakeSession(png=b"fake-png-bytes")
    monkeypatch.setattr(
        "app.modules.scraper.core.browser_scrape.save_screenshot", lambda png: None  # noqa: ARG005
    )
    result = _tool_capture_screenshot("https://example.com", playwright_session=session)
    assert "error" in result


def test_capture_screenshot_surfaces_capture_failure() -> None:
    """A Playwright failure (bad URL, navigation timeout) surfaces as a clean error."""
    session = _FakeSession(error=RuntimeError("navigation timeout"))
    result = _tool_capture_screenshot("https://example.com", playwright_session=session)
    assert "error" in result


def test_capture_screenshot_tool_registered() -> None:
    """Registers capture_screenshot in both the tool schemas and handlers."""
    from app.modules.ai.research_tools import research_tools

    schemas, handlers = research_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "capture_screenshot" in names
    assert "capture_screenshot" in handlers
