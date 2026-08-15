"""needs_spa_fallback decides whether a plain-HTTP fetch's text should be trusted or retried with Playwright — root-caused gaps here (2026-08-10) let two real articles quote a page's raw, un-rendered placeholder text as if it were settled content."""

from __future__ import annotations

import pytest

from app.modules.scraper.crawlers.web_crawler import needs_spa_fallback


@pytest.fixture(autouse=True)
def _spa_fallback_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.config.SPA_FALLBACK_ENABLED", True)


def test_thin_text_needs_fallback() -> None:
    """Under the character floor regardless of markup."""
    assert needs_spa_fallback("short", raw_html="<html></html>") is True


def test_known_framework_root_with_thin_text_needs_fallback() -> None:
    """A recognized framework root (Next.js) with mostly-unrendered text still triggers the fallback."""
    text = "x" * 400  # over _MIN_HTTP_TEXT, under _SPA_TEXT_SUFFICIENT
    raw = '<div id="__next"></div>'
    assert needs_spa_fallback(text, raw_html=raw) is True


def test_custom_app_shell_root_with_thin_text_needs_fallback() -> None:
    """Regression for the Pixel City root-cause shape: a hand-rolled SPA using class="app-shell" (no framework marker) is now recognized too."""
    text = "x" * 400
    raw = '<body><div class="app-shell"><header>...</header></div></body>'
    assert needs_spa_fallback(text, raw_html=raw) is True


def test_loading_placeholder_in_text_needs_fallback_even_with_no_spa_root_marker() -> None:
    """Regression-pin the actual Pixel City incident: raw HTML has no SPA-root marker at all, and the extracted text (683 chars) clears both the thin-text floor and the SPA-root+sufficient-text check — only the loading-placeholder text itself gives it away."""
    text = (
        "Pixel City — Gallery\nRUIZ ART\nCARGANDO...\nSIN OBRAS MINTEADAS AUN\n"
        "PAGINA 1 / 1\nWALLET ACTUAL\nCONSULTANDO...\n" + ("x" * 300)
    )
    raw = '<body><div class="app-shell"><span id="mint-counter">… / 512</span></div></body>'
    assert needs_spa_fallback(text, raw_html=raw) is True


def test_loading_placeholder_alone_triggers_without_any_markup_signal() -> None:
    """The placeholder-text check works even when raw_html is unavailable/empty — a pure text-content signal, unlike the markup-based checks."""
    text = "Loading...\n" + ("x" * 300)
    assert needs_spa_fallback(text, raw_html="") is True


def test_settled_real_content_does_not_need_fallback() -> None:
    """A genuinely long, real article page with no SPA markers and no loading text is trusted as-is."""
    text = "A real news article. " * 50  # well over both thresholds
    raw = "<html><body><article>...</article></body></html>"
    assert needs_spa_fallback(text, raw_html=raw) is False


def test_disabled_flag_short_circuits_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """SPA_FALLBACK_ENABLED=False skips every check, even an otherwise-obvious loading placeholder."""
    monkeypatch.setattr("app.core.config.SPA_FALLBACK_ENABLED", False)
    text = "Loading..." + ("x" * 300)
    assert needs_spa_fallback(text, raw_html='<div id="__next"></div>') is False
