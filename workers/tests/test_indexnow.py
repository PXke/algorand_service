from __future__ import annotations

from app.modules.newspaper import indexnow


def test_article_url_is_path_based_no_hash() -> None:
    url = indexnow.article_url("abc123")
    assert url.endswith("/news/articles/abc123")
    assert "#" not in url


def test_article_url_with_lang() -> None:
    assert indexnow.article_url("x", "fa").endswith("/news/articles/x?lang=fa")
    assert indexnow.article_url("x", "en") == indexnow.article_url("x")


def test_article_urls_includes_translations() -> None:
    urls = indexnow.article_urls("id1", ["fa", "ar", "fa"])
    assert urls == [
        "https://algorand.pxke.me/news/articles/id1",
        "https://algorand.pxke.me/news/articles/id1?lang=fa",
        "https://algorand.pxke.me/news/articles/id1?lang=ar",
    ]


def test_content_change_urls_includes_sitemaps(monkeypatch) -> None:
    monkeypatch.setattr(indexnow.config, "PUBLIC_SITE_URL", "https://algorand.pxke.me")
    urls = indexnow.content_change_urls("id1", translation_langs=["fa"])
    assert "https://algorand.pxke.me/sitemap.xml" in urls
    assert "https://algorand.pxke.me/sitemap-news.xml" in urls
    assert "https://algorand.pxke.me/news/articles/id1?lang=fa" in urls


def test_ping_noop_without_urls(monkeypatch) -> None:
    called = {"n": 0}
    monkeypatch.setattr(indexnow.httpx, "post", lambda *a, **k: called.__setitem__("n", 1))
    indexnow.ping([])
    indexnow.ping([""])
    assert called["n"] == 0


def test_ping_noop_without_key(monkeypatch) -> None:
    called = {"n": 0}
    monkeypatch.setattr(indexnow.config, "INDEXNOW_KEY", "")
    monkeypatch.setattr(indexnow.httpx, "post", lambda *a, **k: called.__setitem__("n", 1))
    indexnow.ping(["https://algorand.pxke.me/news/articles/x"])
    assert called["n"] == 0


def test_ping_dedupes_urls(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, json, timeout):
        captured["json"] = json

        class _R:
            status_code = 200

        return _R()

    monkeypatch.setattr(indexnow.config, "INDEXNOW_KEY", "KEY123")
    monkeypatch.setattr(indexnow.config, "PUBLIC_SITE_URL", "https://algorand.pxke.me")
    monkeypatch.setattr(indexnow.httpx, "post", fake_post)
    dup = "https://algorand.pxke.me/news/articles/x"
    indexnow.ping([dup, dup, ""])
    assert captured["json"]["urlList"] == [dup]


def test_ping_posts_expected_payload(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json

        class _R:
            status_code = 200

        return _R()

    monkeypatch.setattr(indexnow.config, "INDEXNOW_KEY", "KEY123")
    monkeypatch.setattr(indexnow.config, "PUBLIC_SITE_URL", "https://algorand.pxke.me")
    monkeypatch.setattr(indexnow.httpx, "post", fake_post)
    indexnow.ping(["https://algorand.pxke.me/news/articles/x"])
    assert captured["url"] == indexnow._ENDPOINT
    assert captured["json"]["host"] == "algorand.pxke.me"
    assert captured["json"]["key"] == "KEY123"
    assert captured["json"]["keyLocation"] == "https://algorand.pxke.me/KEY123.txt"
    assert captured["json"]["urlList"] == ["https://algorand.pxke.me/news/articles/x"]


def test_ping_retries_on_http_error(monkeypatch) -> None:
    attempts = {"n": 0}

    def fake_post(url, json, timeout):
        attempts["n"] += 1

        class _R:
            status_code = 503

        return _R()

    monkeypatch.setattr(indexnow.config, "INDEXNOW_KEY", "KEY123")
    monkeypatch.setattr(indexnow.config, "PUBLIC_SITE_URL", "https://algorand.pxke.me")
    monkeypatch.setattr(indexnow.httpx, "post", fake_post)
    monkeypatch.setattr(indexnow.time, "sleep", lambda _s: None)
    indexnow.ping(["https://algorand.pxke.me/news/articles/x"])
    assert attempts["n"] == indexnow._MAX_ATTEMPTS
