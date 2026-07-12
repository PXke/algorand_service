from __future__ import annotations

from app.modules.seo import indexnow


def test_article_url_with_lang() -> None:
    assert indexnow.article_url("x", "fa").endswith("/news/articles/x?lang=fa")
    assert indexnow.article_url("x", "en") == indexnow.article_url("x")


def test_translation_change_urls(monkeypatch) -> None:
    monkeypatch.setattr(
        indexnow.settings, "public_site_url", "https://algorand.pxke.me"
    )
    urls = indexnow.translation_change_urls("id1", "fa")
    assert urls == [
        "https://algorand.pxke.me/news/articles/id1?lang=fa",
        "https://algorand.pxke.me/sitemap.xml",
        "https://algorand.pxke.me/sitemap-news.xml",
    ]


def test_ping_article_includes_all_translation_urls(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, json, timeout):
        captured["json"] = json

        class _R:
            status_code = 200

        return _R()

    monkeypatch.setattr(indexnow.settings, "indexnow_key", "KEY123")
    monkeypatch.setattr(
        indexnow.settings, "public_site_url", "https://algorand.pxke.me"
    )
    monkeypatch.setattr(indexnow.httpx, "post", fake_post)
    indexnow.ping_article("id1", translation_langs=["fa", "ar"])
    url_list = captured["json"]["urlList"]
    assert "https://algorand.pxke.me/news/articles/id1" in url_list
    assert "https://algorand.pxke.me/news/articles/id1?lang=fa" in url_list
    assert "https://algorand.pxke.me/news/articles/id1?lang=ar" in url_list
    assert "https://algorand.pxke.me/sitemap-news.xml" in url_list
