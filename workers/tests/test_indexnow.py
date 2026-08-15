"""IndexNow URL construction and ping payloads (workers-side)."""

from __future__ import annotations

from typing import Any

import pytest
from algorand_shared import indexnow as shared_indexnow
from app.modules.newspaper import indexnow


def test_article_url_is_path_based_no_hash() -> None:
    """Builds a path-based article URL with no hash fragment."""
    url = indexnow.article_url("abc123")
    assert url.endswith("/news/articles/abc123")
    assert "#" not in url


def test_article_url_with_lang() -> None:
    """Path-segments a non-English locale (matching SSR/sitemap) and omits it for English.

    Root-caused live 2026-08-10 via Bing Webmaster Tools: every URL IndexNow
    submitted for a translated locale still used the pre-migration ?lang=
    query param, and every URL used the raw article id rather than its slug.
    """
    assert indexnow.article_url("x", "fa").endswith("/fa/news/articles/x")
    assert "?lang=" not in indexnow.article_url("x", "fa")
    assert indexnow.article_url("x", "en") == indexnow.article_url("x")


def test_article_url_prefers_slug_over_id() -> None:
    """Uses the permanent slug when given one, not the raw article id."""
    assert indexnow.article_url("x", slug="real-slug").endswith("/news/articles/real-slug")
    assert indexnow.article_url("x", "fa", slug="real-slug").endswith("/fa/news/articles/real-slug")


def test_article_urls_includes_translations() -> None:
    """Builds one URL per translation locale plus the base article URL, deduped."""
    urls = indexnow.article_urls("id1", ["fa", "ar", "fa"])
    assert urls == [
        "https://algorand.pxke.me/news/articles/id1",
        "https://algorand.pxke.me/fa/news/articles/id1",
        "https://algorand.pxke.me/ar/news/articles/id1",
    ]


def test_content_change_urls_includes_sitemaps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Includes both sitemaps and translation URLs in the content-change URL set."""
    monkeypatch.setattr(indexnow.config, "PUBLIC_SITE_URL", "https://algorand.pxke.me")
    urls = indexnow.content_change_urls("id1", translation_langs=["fa"])
    assert "https://algorand.pxke.me/sitemap.xml" in urls
    assert "https://algorand.pxke.me/sitemap-news.xml" in urls
    assert "https://algorand.pxke.me/fa/news/articles/id1" in urls


def test_ping_noop_without_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the HTTP POST when the URL list is empty or contains only blanks."""
    called = {"n": 0}
    monkeypatch.setattr(shared_indexnow.httpx, "post", lambda *_a, **_k: called.__setitem__("n", 1))
    indexnow.ping([])
    indexnow.ping([""])
    assert called["n"] == 0


def test_ping_noop_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the HTTP POST when no IndexNow key is configured."""
    called = {"n": 0}
    monkeypatch.setattr(indexnow.config, "INDEXNOW_KEY", "")
    monkeypatch.setattr(shared_indexnow.httpx, "post", lambda *_a, **_k: called.__setitem__("n", 1))
    indexnow.ping(["https://algorand.pxke.me/news/articles/x"])
    assert called["n"] == 0


def test_ping_dedupes_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dedupes repeated and blank URLs before posting the urlList."""
    captured: dict = {}

    def fake_post(_url: str, json: dict, timeout: float) -> Any:  # noqa: ARG001, ANN401 -- name must match the real callee's keyword arg
        captured["json"] = json

        class _R:
            status_code = 200

        return _R()

    monkeypatch.setattr(indexnow.config, "INDEXNOW_KEY", "KEY123")
    monkeypatch.setattr(indexnow.config, "PUBLIC_SITE_URL", "https://algorand.pxke.me")
    monkeypatch.setattr(shared_indexnow.httpx, "post", fake_post)
    dup = "https://algorand.pxke.me/news/articles/x"
    indexnow.ping([dup, dup, ""])
    assert captured["json"]["urlList"] == [dup]


def test_ping_posts_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Posts the expected host/key/keyLocation/urlList payload to the IndexNow endpoint."""
    captured: dict = {}

    def fake_post(url: str, json: dict, timeout: float) -> Any:  # noqa: ARG001, ANN401 -- name must match the real callee's keyword arg
        captured["url"] = url
        captured["json"] = json

        class _R:
            status_code = 200

        return _R()

    monkeypatch.setattr(indexnow.config, "INDEXNOW_KEY", "KEY123")
    monkeypatch.setattr(indexnow.config, "PUBLIC_SITE_URL", "https://algorand.pxke.me")
    monkeypatch.setattr(shared_indexnow.httpx, "post", fake_post)
    indexnow.ping(["https://algorand.pxke.me/news/articles/x"])
    assert captured["url"] == shared_indexnow._ENDPOINT
    assert captured["json"]["host"] == "algorand.pxke.me"
    assert captured["json"]["key"] == "KEY123"
    assert captured["json"]["keyLocation"] == "https://algorand.pxke.me/KEY123.txt"
    assert captured["json"]["urlList"] == ["https://algorand.pxke.me/news/articles/x"]


def test_ping_retries_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries up to the max attempt count when IndexNow returns a server error."""
    attempts = {"n": 0}

    def fake_post(_url: str, json: dict, timeout: float) -> Any:  # noqa: ARG001, ANN401 -- name must match the real callee's keyword arg
        attempts["n"] += 1

        class _R:
            status_code = 503

        return _R()

    monkeypatch.setattr(indexnow.config, "INDEXNOW_KEY", "KEY123")
    monkeypatch.setattr(indexnow.config, "PUBLIC_SITE_URL", "https://algorand.pxke.me")
    monkeypatch.setattr(shared_indexnow.httpx, "post", fake_post)
    monkeypatch.setattr(shared_indexnow.time, "sleep", lambda _s: None)
    indexnow.ping(["https://algorand.pxke.me/news/articles/x"])
    assert attempts["n"] == shared_indexnow._MAX_ATTEMPTS
