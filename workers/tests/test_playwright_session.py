"""Persistent per-compose PlaywrightSession: fetch_url always renders HTML through it when one is available (2026-08-11), instead of gating on the thin/SPA-shape heuristic -- a heuristic that had real false negatives (hesab.com's client-rendered numbers)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.ai.research_tools import _maybe_render_spa_fallback
from app.modules.scraper.core.browser_scrape import BrowserPageResult, maybe_start_session


class _FakeResp:
    def __init__(self, text: str = "<html>thin shell</html>") -> None:
        self.text = text


def test_maybe_render_spa_fallback_always_renders_when_session_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a normal-looking page (heuristic would say no) gets rendered when a session is available -- that's the whole point of always-on rendering."""
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled", lambda: True
    )
    session = MagicMock()
    session.fetch.return_value = BrowserPageResult(
        title="Rendered",
        text="the real client-rendered number: 3.5M",
        final_url="https://example.com/",
        engine="playwright-session",
    )
    title, text, links, _base = _maybe_render_spa_fallback(
        _FakeResp("<html><body>plenty of normal-looking text here</body></html>"),
        base="https://example.com",
        title="Original",
        text="original text",
        plain_text="plenty of normal-looking text here",
        links=[{"text": "a", "url": "https://example.com/a"}],
        playwright_session=session,
    )
    assert title == "Rendered"
    assert "3.5M" in text
    assert links == [{"text": "a", "url": "https://example.com/a"}]  # link list carried through unchanged
    session.fetch.assert_called_once_with("https://example.com")


def test_maybe_render_spa_fallback_falls_back_to_original_on_render_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed render beats no result at all -- original httpx-extracted content survives."""
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled", lambda: True
    )
    session = MagicMock()
    session.fetch.side_effect = RuntimeError("navigation timeout")
    _title, text, _links, _base = _maybe_render_spa_fallback(
        _FakeResp(),
        base="https://example.com",
        title="Original",
        text="original text",
        plain_text="original text",
        links=[],
        playwright_session=session,
    )
    assert text == "original text"


def test_maybe_render_spa_fallback_skips_entirely_when_feature_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CRAWLER_WEB_SPA_ENABLED kill switch still wins even with a live session passed in."""
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled", lambda: False
    )
    session = MagicMock()
    title, _text, _links, _base = _maybe_render_spa_fallback(
        _FakeResp(),
        base="https://example.com",
        title="Original",
        text="original text",
        plain_text="original text",
        links=[],
        playwright_session=session,
    )
    assert title == "Original"
    session.fetch.assert_not_called()


def test_maybe_render_spa_fallback_uses_heuristic_when_no_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No persistent session (e.g. it failed to start) -- falls back to the old heuristic-gated one-shot render, not a hard failure."""
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled", lambda: True
    )
    monkeypatch.setattr(
        "app.modules.scraper.crawlers.web_crawler.needs_spa_fallback", lambda *_a, **_kw: False
    )
    title, text, _links, _base = _maybe_render_spa_fallback(
        _FakeResp(),
        base="https://example.com",
        title="Original",
        text="original text",
        plain_text="original text",
        links=[],
        playwright_session=None,
    )
    assert title == "Original"
    assert text == "original text"


def test_maybe_start_session_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The feature flag is a real kill switch -- no session, no browser process spun up, when disabled."""
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled", lambda: False
    )
    assert maybe_start_session() is None


def test_maybe_start_session_returns_none_on_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A compose must still run even if Playwright/Chromium can't launch -- never raises out to the caller."""
    monkeypatch.setattr(
        "app.modules.scraper.crawler_registry.is_web_spa_enabled", lambda: True
    )

    def _boom() -> None:
        raise RuntimeError("chromium not installed")

    monkeypatch.setattr(
        "app.modules.scraper.core.browser_scrape.PlaywrightSession", _boom
    )
    assert maybe_start_session() is None
