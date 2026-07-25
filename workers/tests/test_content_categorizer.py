"""Keyword-fallback content categorization."""

from __future__ import annotations

import pytest

from app.modules.ai.content_categorizer import (
    _fallback_categories,
    _fallback_category,
    categorize_content,
)


def test_fallback_category_news() -> None:
    """Classifies announcement/launch/update language on a /news path as "news"."""
    text = "Breaking announcement: partnership launch update"
    assert _fallback_category(text, "https://algorand.com/news") == "news"


def test_fallback_categories_multi() -> None:
    """Returns multiple keyword-matched categories including "tool" for SDK/dev-tool text."""
    text = "Algorand wallet SDK developer tool api library"
    cats = _fallback_categories(text, "https://example.com/dev")
    assert "tool" in cats
    assert len(cats) >= 2


def test_categorize_content_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to keyword categorization when Mistral is not configured."""
    monkeypatch.setattr("app.core.config.mistral_configured", lambda: False)
    monkeypatch.setattr("app.core.config.CONTENT_CATEGORIZATION_ENABLED", True)
    cat = categorize_content("Algorand wallet SDK developer tool", "https://example.com")
    assert cat in ("tool", "service", "generic")
