from __future__ import annotations

from app.modules.newspaper import indexnow


def test_article_url_is_path_based_no_hash() -> None:
    url = indexnow.article_url("abc123")
    assert url.endswith("/news/articles/abc123")
    assert "#" not in url  # canonical path form, not the legacy /#/ route


def test_ping_noop_without_urls(monkeypatch) -> None:
    # Should return without attempting any network call.
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


def test_ping_posts_expected_payload(monkeypatch) -> None:
    captured = {}

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
    assert captured["json"]["host"] == "algorand.pxke.me"
    assert captured["json"]["key"] == "KEY123"
    assert captured["json"]["keyLocation"].endswith("/KEY123.txt")
    assert captured["json"]["urlList"] == ["https://algorand.pxke.me/news/articles/x"]
