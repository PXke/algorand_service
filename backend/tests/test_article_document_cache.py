"""SSR article-document route: cache-aside behavior.

The Cassandra detail fetch + render is cache-asided (app.core.cache.cached_json,
short TTL) independent of the per-request analytics side effects, which must
still run on every real hit.
"""

from __future__ import annotations

import pytest

from app.core.http import QueryParams, Request
from app.modules.news.models.schemas import ArticleDetail
from app.modules.seo.api import routes as seo_routes


class FakeRedis:
    """In-memory stand-in for the redis-py client, covering the get/set surface app.core.cache uses."""

    def __init__(self) -> None:
        """Start with an empty in-process key/value store."""
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        """Return the stored value for a key, or None if absent."""
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:  # noqa: ARG002 -- ex accepted, not enforced (no real TTL in-memory)
        """Set a key's value; ex (TTL) is accepted but not enforced."""
        self.store[key] = value
        return True


@pytest.fixture
def _fake_cache_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Back app.core.cache's Redis client with an in-memory fake."""
    fake = FakeRedis()
    monkeypatch.setattr("app.core.cache._client", lambda: fake)
    return fake


def _article(**kw: object) -> ArticleDetail:
    base = {
        "article_id": "abc123",
        "service_id": "svc",
        "title": "Algorand Foundation Launches New Tool",
        "summary": "A concise summary of the announcement.",
        "body": "Body text.",
        "published_at_epoch": 1_750_000_000,
        "tags": ["sdk"],
        "slug": "algorand-foundation-launches-new-tool",
    }
    base.update(kw)
    return ArticleDetail(**base)


def _req(article_id: str) -> Request:
    return Request(
        method="GET",
        headers={},
        query_params=QueryParams({}),  # type: ignore[arg-type]
        path_params={"article_id": article_id},
    )


def _wire(monkeypatch: pytest.MonkeyPatch, detail: ArticleDetail, calls: list[str]) -> None:
    def fake_get_article(
        article_id: str, lang: str | None = None, **_: object
    ) -> ArticleDetail | None:
        _ = lang
        calls.append(article_id)
        return detail

    monkeypatch.setattr(seo_routes.news, "resolve_slug", lambda _slug: None)
    monkeypatch.setattr(seo_routes.news, "get_article", fake_get_article)
    monkeypatch.setattr(seo_routes.news, "translation_langs_for", lambda _id: [])
    monkeypatch.setattr(seo_routes.news, "list_feed", lambda **_: [])


@pytest.mark.usefixtures("_fake_cache_redis")
def test_article_document_cache_hit_avoids_second_get_article_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeated request for the same article/lang must not re-hit NewsService.get_article -- the detail fetch + render is cached for _ARTICLE_DOC_CACHE_TTL seconds."""
    calls: list[str] = []
    detail = _article()
    _wire(monkeypatch, detail, calls)

    resp1 = seo_routes.article(_req(detail.slug))  # type: ignore[arg-type]
    resp2 = seo_routes.article(_req(detail.slug))  # type: ignore[arg-type]

    assert calls == ["algorand-foundation-launches-new-tool"]  # only ONE real fetch
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.description == resp2.description
    assert detail.title in resp1.description


@pytest.mark.usefixtures("_fake_cache_redis")
def test_article_document_cache_miss_for_404_is_not_cached_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 'no such article' result is itself cached (avoids re-hitting Cassandra for a repeated bad/probing URL), but still recomputes once the cache entry is gone -- it isn't a permanent tombstone."""
    calls: list[str] = []

    def fake_get_article(
        article_id: str, lang: str | None = None, **_: object
    ) -> ArticleDetail | None:
        _ = lang
        calls.append(article_id)
        return None

    monkeypatch.setattr(seo_routes.news, "resolve_slug", lambda _slug: None)
    monkeypatch.setattr(seo_routes.news, "get_article", fake_get_article)
    monkeypatch.setattr(seo_routes.news, "translation_langs_for", lambda _id: [])
    monkeypatch.setattr(seo_routes.news, "list_feed", lambda **_: [])
    monkeypatch.setattr(seo_routes, "_article_tombstoned", lambda _id: False)

    resp1 = seo_routes.article(_req("missing-slug"))
    resp2 = seo_routes.article(_req("missing-slug"))

    assert calls == ["missing-slug"]  # second 404 served from cache, not re-fetched
    assert resp1.status_code == 404
    assert resp2.status_code == 404


def _localized_req(lang: str, article_id: str) -> Request:
    return Request(
        method="GET",
        headers={},
        query_params=QueryParams({}),  # type: ignore[arg-type]
        path_params={"lang": lang, "article_id": article_id},
    )


@pytest.mark.usefixtures("_fake_cache_redis")
def test_untranslated_locale_url_renders_as_the_english_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/ru/news/articles/<slug> for an article with NO Russian translation must not be an indexable, self-canonical duplicate of the English article.

    get_article falls back to the English text for a missing translation, so
    the document is rendered AS English: canonical points at the bare English
    URL, og:locale/content-language/<html lang> say English, and the hreflang
    set (which never listed ru) stays consistent with the canonical. Only the
    SPA's dedup marker keeps the browser's real /ru/ path. Live 2026-09-05:
    every one of these served `<html lang="ru">` + English text + canonical
    https://algorand.pxke.me/ru/... with no noindex.
    """
    calls: list[str] = []
    detail = _article()
    _wire(monkeypatch, detail, calls)
    monkeypatch.setattr(seo_routes.news, "translation_langs_for", lambda _id: ["fr"])

    resp = seo_routes.article_localized(_localized_req("ru", detail.slug))  # type: ignore[arg-type]

    assert resp.status_code == 200
    doc = resp.description
    assert 'rel="canonical" href="https://algorand.pxke.me/news/articles/' in doc
    assert 'property="og:url" content="https://algorand.pxke.me/news/articles/' in doc
    # The /ru/ path appears exactly once: the SPA dedup marker (asserted
    # below) -- never in canonical, og:url, hreflang or the translation picker.
    assert doc.count("/ru/news/articles/") == 1
    assert '<html lang="en"' in doc
    assert 'property="og:locale" content="en_US"' in doc
    assert 'http-equiv="content-language" content="en"' in doc
    assert 'hreflang="fr"' in doc
    assert 'hreflang="ru"' not in doc
    # The SPA dedup marker still names the path the browser is actually at.
    assert 'pxke_ssr_pv","/ru/news/articles/' in doc


@pytest.mark.usefixtures("_fake_cache_redis")
def test_translated_locale_url_still_renders_in_that_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control for the test above: a locale that DOES have a translation keeps its own canonical/lang."""
    calls: list[str] = []
    detail = _article()
    _wire(monkeypatch, detail, calls)
    monkeypatch.setattr(seo_routes.news, "translation_langs_for", lambda _id: ["fr"])

    resp = seo_routes.article_localized(_localized_req("fr", detail.slug))  # type: ignore[arg-type]

    assert resp.status_code == 200
    doc = resp.description
    assert 'rel="canonical" href="https://algorand.pxke.me/fr/news/articles/' in doc
    assert '<html lang="fr"' in doc
    assert 'property="og:locale" content="fr_FR"' in doc


def test_article_document_cache_expires_after_ttl(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Once the cached entry ages out (simulated here by clearing the backing store, same as a real Redis TTL expiry), the next request recomputes rather than serving the render forever."""
    fake_redis: FakeRedis = request.getfixturevalue("_fake_cache_redis")
    calls: list[str] = []
    detail = _article()
    _wire(monkeypatch, detail, calls)

    seo_routes.article(_req(detail.slug))  # type: ignore[arg-type]
    assert calls == ["algorand-foundation-launches-new-tool"]

    fake_redis.store.clear()  # simulate the TTL elapsing

    seo_routes.article(_req(detail.slug))  # type: ignore[arg-type]
    assert calls == ["algorand-foundation-launches-new-tool"] * 2
