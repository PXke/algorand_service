"""PlaywrightSession.capture_screenshot's own higher-fidelity context (root-caused 2026-08-26: screenshots looked low-resolution because no context in browser_scrape.py ever set a device_scale_factor) -- must use a SEPARATE context from the shared self._context every other method uses, and must always close that context, success or failure."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.modules.scraper.core.browser_scrape import (
    _SCREENSHOT_DEVICE_SCALE_FACTOR,
    BrowserScrapeError,
    PlaywrightSession,
)


def _bare_session() -> PlaywrightSession:
    """A PlaywrightSession with mocked _browser/_context, bypassing __init__'s real Chromium launch."""
    session = PlaywrightSession.__new__(PlaywrightSession)
    session._closed = False
    session._browser = MagicMock()
    session._context = MagicMock()
    session._interactive_page = None
    session._storage_state_path = None
    return session


def test_capture_screenshot_uses_a_fresh_context_not_the_shared_one() -> None:
    """capture_screenshot must open its own retina-scale context via self._browser, never touching the shared self._context that fetch/click/type/interactive all reuse."""
    session = _bare_session()
    fresh_context = MagicMock()
    session._browser.new_context.return_value = fresh_context
    page = fresh_context.new_page.return_value
    page.screenshot.return_value = b"png-bytes"

    with (
        patch.object(session, "_goto_and_settle"),
        patch("app.modules.scraper.core.browser_scrape._expand_collapsed_content"),
    ):
        result = session.capture_screenshot("https://example.com")

    assert result == b"png-bytes"
    session._browser.new_context.assert_called_once()
    kwargs = session._browser.new_context.call_args.kwargs
    assert kwargs["device_scale_factor"] == _SCREENSHOT_DEVICE_SCALE_FACTOR
    session._context.new_page.assert_not_called()  # the shared context is never touched
    fresh_context.close.assert_called_once()


def test_capture_screenshot_closes_its_context_even_on_failure() -> None:
    """A navigation failure must not leak the screenshot-only context."""
    session = _bare_session()
    fresh_context = MagicMock()
    session._browser.new_context.return_value = fresh_context

    with (
        patch.object(session, "_goto_and_settle", side_effect=BrowserScrapeError("nav timeout")),
        pytest.raises(BrowserScrapeError),
    ):
        session.capture_screenshot("https://example.com")

    fresh_context.close.assert_called_once()
