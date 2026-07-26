"""IndexNow URL construction and ping payloads."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.seo import indexnow


def test_article_url_with_lang() -> None:
    """Appends ?lang= for non-English, and matches the bare URL for English."""
    assert indexnow.article_url("x", "fa").endswith("/news/articles/x?lang=fa")
    assert indexnow.article_url("x", "en") == indexnow.article_url("x")


def test_ping_article_includes_all_translation_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Includes the base article URL, each translation URL, and the news sitemap in the IndexNow ping."""
    captured: dict = {}

    def fake_post(_url: str, json: dict, timeout: float) -> Any:  # noqa: ARG001, ANN401 -- name must match the real callee's keyword arg; fake httpx response
        captured["json"] = json

        class _R:
            status_code = 200

        return _R()

    monkeypatch.setattr(indexnow.settings, "indexnow_key", "KEY123")
    monkeypatch.setattr(indexnow.settings, "public_site_url", "https://algorand.pxke.me")
    monkeypatch.setattr(indexnow.httpx, "post", fake_post)
    indexnow.ping_article("id1", translation_langs=["fa", "ar"])
    url_list = captured["json"]["urlList"]
    assert "https://algorand.pxke.me/news/articles/id1" in url_list
    assert "https://algorand.pxke.me/news/articles/id1?lang=fa" in url_list
    assert "https://algorand.pxke.me/news/articles/id1?lang=ar" in url_list
    assert "https://algorand.pxke.me/sitemap-news.xml" in url_list
