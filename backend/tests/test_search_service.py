"""Feed-scan search fallback and Typesense tuning (synonyms, prefix, typos)."""

from __future__ import annotations

import pytest

from app.core.typesense_client import expanded_search_terms
from app.modules.news.services.news_service import NewsService
from app.modules.news.stores.base import StoredArticle
from app.modules.news.stores.memory import InMemoryArticleStore
from app.modules.search.services.search_service import (
    SearchService,
    _feed_article_matches,
    _localized_view,
    _typesense_num_typos,
    _typesense_prefix_enabled,
)


def test_search_feed_scan_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to feed-scan search (with a matching snippet) when Typesense is unconfigured."""
    monkeypatch.setattr(
        "app.modules.search.services.search_service.get_typesense_client",
        lambda: None,
    )
    store = InMemoryArticleStore()
    store.insert(
        StoredArticle(
            article_id="1",
            service_id="svc",
            title="Algorand governance update",
            summary="Weekly recap",
            body="body",
            published_at_epoch=1,
        )
    )
    news = NewsService(store=store)
    result = SearchService(news_service=news).search("governance")
    assert result.engine == "feed_scan"
    assert len(result.items) == 1
    assert result.items[0].title.startswith("Algorand")
    assert result.items[0].snippet is not None
    assert "governance" in result.items[0].snippet.lower()


def test_search_feed_scan_caps_rows_scanned_independent_of_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The feed-scan fallback scans a small, fixed number of rows regardless of the caller's requested `limit` -- Typesense being unreachable is exactly when load is already spiking, so a large `limit` must not balloon the scan (previously hardcoded to 100 rows no matter what)."""
    monkeypatch.setattr(
        "app.modules.search.services.search_service.get_typesense_client",
        lambda: None,
    )
    from app.modules.search.services import search_service as search_service_mod

    seen_limits: list[int] = []

    class _SpyStore(InMemoryArticleStore):
        def list_feed(self, *, feed_bucket: str = "main", limit: int = 50) -> list[StoredArticle]:
            seen_limits.append(limit)
            return super().list_feed(feed_bucket=feed_bucket, limit=limit)

    store = _SpyStore()
    for i in range(50):
        store.insert(
            StoredArticle(
                article_id=str(i),
                service_id="svc",
                title="Algorand governance update",
                summary="Weekly recap",
                body="body",
                published_at_epoch=i,
            )
        )
    news = NewsService(store=store)
    result = SearchService(news_service=news).search("governance", limit=100)

    assert result.engine == "feed_scan"
    # The scan limit is the module's fixed cap, not the caller's requested 100.
    assert seen_limits == [search_service_mod._FEED_SCAN_LIMIT]
    assert search_service_mod._FEED_SCAN_LIMIT < 100
    assert len(result.items) <= search_service_mod._FEED_SCAN_LIMIT


def test_localized_view_overlays_from_translated_titles() -> None:
    """The feed-scan fallback's per-locale view reads translated_titles (title+summary only, migration 087) -- a feed-listing StoredArticle never carries the full translations map, and body always stays the (empty) English feed-row body regardless of lang."""
    import json

    article = StoredArticle(
        article_id="1",
        service_id="svc",
        title="English title",
        summary="English summary",
        body="",
        published_at_epoch=1,
        translated_titles={
            "fr": json.dumps({"title": "Titre francais", "summary": "Resume francais"})
        },
    )

    view = _localized_view(article, "fr")

    assert view.title == "Titre francais"
    assert view.summary == "Resume francais"
    assert view.body == ""


def test_localized_view_falls_back_to_english_without_a_stored_translation() -> None:
    """No translated_titles entry for the requested lang -- falls back to English fields, no crash."""
    article = StoredArticle(
        article_id="1",
        service_id="svc",
        title="English title",
        summary="English summary",
        body="",
        published_at_epoch=1,
    )

    view = _localized_view(article, "fr")

    assert view.title == "English title"
    assert view.summary == "English summary"


def test_feed_scan_usa_does_not_match_usability(monkeypatch: pytest.MonkeyPatch) -> None:
    """Searching "USA" does not spuriously match an article containing "usability"."""
    monkeypatch.setattr(
        "app.modules.search.services.search_service.get_typesense_client",
        lambda: None,
    )
    store = InMemoryArticleStore()
    store.insert(
        StoredArticle(
            article_id="1",
            service_id="svc",
            title="Usability study on wallets",
            summary="UX research",
            body="Improving usability for newcomers",
            published_at_epoch=1,
        )
    )
    news = NewsService(store=store)
    result = SearchService(news_service=news).search("USA")
    assert result.engine == "feed_scan"
    assert result.items == []


def test_feed_scan_usa_matches_us_via_synonym(monkeypatch: pytest.MonkeyPatch) -> None:
    """Searching "USA" matches an article that only says "US" via the geo-usa synonym cluster."""
    monkeypatch.setattr(
        "app.modules.search.services.search_service.get_typesense_client",
        lambda: None,
    )
    store = InMemoryArticleStore()
    store.insert(
        StoredArticle(
            article_id="1",
            service_id="svc",
            title="Treasury flows",
            summary="Weekly recap",
            body="Investors sent funds back to US markets overnight.",
            published_at_epoch=1,
        )
    )
    news = NewsService(store=store)
    result = SearchService(news_service=news).search("USA")
    assert result.engine == "feed_scan"
    assert len(result.items) == 1
    assert "US" in (result.items[0].snippet or "")


def test_expanded_search_terms_includes_us_for_usa() -> None:
    """Expanding "USA" includes both "usa" and "us" as search terms."""
    terms = expanded_search_terms("USA")
    assert "usa" in terms
    assert "us" in terms


def test_typesense_tuning_for_short_acronyms() -> None:
    """Disables prefix matching and typo tolerance for short all-caps acronyms like "USA"."""
    assert _typesense_prefix_enabled("USA") is False
    assert _typesense_prefix_enabled("governance") is True
    assert _typesense_num_typos("USA") == 0
    assert _typesense_num_typos("governance") == 2


def test_feed_article_matches_word_boundary() -> None:
    """Matches a search term only on word boundaries, not as a substring of a longer word."""
    article = StoredArticle(
        article_id="1",
        service_id="svc",
        title="Usability",
        summary="",
        body="",
        published_at_epoch=1,
    )
    assert not _feed_article_matches(article, ["usa"])
    assert _feed_article_matches(
        StoredArticle(
            article_id="2",
            service_id="svc",
            title="Markets",
            summary="",
            body="back to US soon",
            published_at_epoch=1,
        ),
        ["us"],
    )


def test_parse_highlights_prefers_body_snippet() -> None:
    """Picks the body highlight as the snippet while keeping the title highlight separate."""
    from app.modules.search.services.search_service import _parse_highlights

    title_hl, snippet = _parse_highlights(
        {
            "highlights": [
                {"field": "title", "snippet": "<mark>Governance</mark> vote"},
                {"field": "summary", "snippet": "short <mark>governance</mark> note"},
                {"field": "body", "snippet": "long <mark>governance</mark> story in the body"},
            ]
        }
    )
    assert title_hl == "<mark>Governance</mark> vote"
    assert snippet == "long <mark>governance</mark> story in the body"


def test_search_typesense_parses_highlights(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uses Typesense when configured, forwarding tuned query params and parsing hit highlights."""

    class _FakeDocuments:
        def search(self, params: tuple) -> dict:
            assert params["highlight_fields"] == "title,summary,body"
            assert params["query_by"] == "title,summary,body,tokens"
            assert params["query_by_weights"] == "4,2,1,6"
            assert params["num_typos"] == 0
            return {
                "hits": [
                    {
                        "document": {
                            "id": "abc",
                            "title": "Governance vote",
                            "summary": "Weekly recap",
                            "service_id": "svc",
                            "published_at": 1700000000,
                        },
                        "text_match": 99,
                        "highlights": [
                            {
                                "field": "body",
                                "snippet": "the <mark>governance</mark> proposal passed",
                            }
                        ],
                    }
                ]
            }

    class _FakeCollection:
        documents = _FakeDocuments()

    class _FakeCollections:
        def __getitem__(self, _name: str) -> _FakeCollection:
            return _FakeCollection()

    class _FakeClient:
        collections = _FakeCollections()

    monkeypatch.setattr(
        "app.modules.search.services.search_service.get_typesense_client",
        lambda: _FakeClient(),
    )
    monkeypatch.setattr(
        "app.modules.search.services.search_service.ensure_articles_collection",
        lambda: True,
    )
    result = SearchService(news_service=NewsService(store=InMemoryArticleStore())).search("USA")
    assert result.engine == "typesense"
    assert len(result.items) == 1
    assert result.items[0].snippet == "the <mark>governance</mark> proposal passed"


def test_search_typesense_surfaces_slug_on_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Typesense document carrying a `slug` field surfaces it on the SearchHit, so the frontend builds a proper slug URL instead of falling back to the raw article_id.

    Root-caused 2026-08-26: the `articles` collection schema never declared
    a `slug` field at all, so every indexed document (including the
    "Goana" article, which has a perfectly good slug in Cassandra) had no
    slug key whatsoever, and every search result silently fell back to a
    raw-UUID URL.
    """

    class _FakeDocuments:
        def search(self, _params: dict) -> dict:
            return {
                "hits": [
                    {
                        "document": {
                            "id": "9f0a5e92-0220-486c-8c50-3d25d1d19b96",
                            "title": "Al Goanna launches NFT-backed loans",
                            "summary": "Summary",
                            "service_id": "svc",
                            "published_at": 1700000000,
                            "slug": "al-goanna-launches-nft-backed-loans-and-40-000-algo-staking-battles",
                        },
                        "text_match": 99,
                    }
                ]
            }

    class _FakeCollection:
        documents = _FakeDocuments()

    class _FakeCollections:
        def __getitem__(self, _name: str) -> _FakeCollection:
            return _FakeCollection()

    class _FakeClient:
        collections = _FakeCollections()

    monkeypatch.setattr(
        "app.modules.search.services.search_service.get_typesense_client",
        lambda: _FakeClient(),
    )
    monkeypatch.setattr(
        "app.modules.search.services.search_service.ensure_articles_collection",
        lambda: True,
    )
    result = SearchService(news_service=NewsService(store=InMemoryArticleStore())).search("goana")
    assert result.engine == "typesense"
    assert len(result.items) == 1
    assert (
        result.items[0].slug
        == "al-goanna-launches-nft-backed-loans-and-40-000-algo-staking-battles"
    )


def test_search_typesense_slug_is_none_when_document_lacks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document indexed before the slug backfill (no `slug` key at all) surfaces slug=None rather than an empty string or KeyError."""

    class _FakeDocuments:
        def search(self, _params: dict) -> dict:
            return {
                "hits": [
                    {
                        "document": {
                            "id": "abc",
                            "title": "Some article",
                            "summary": "Summary",
                            "service_id": "svc",
                            "published_at": 1700000000,
                        },
                        "text_match": 1,
                    }
                ]
            }

    class _FakeCollection:
        documents = _FakeDocuments()

    class _FakeCollections:
        def __getitem__(self, _name: str) -> _FakeCollection:
            return _FakeCollection()

    class _FakeClient:
        collections = _FakeCollections()

    monkeypatch.setattr(
        "app.modules.search.services.search_service.get_typesense_client",
        lambda: _FakeClient(),
    )
    monkeypatch.setattr(
        "app.modules.search.services.search_service.ensure_articles_collection",
        lambda: True,
    )
    result = SearchService(news_service=NewsService(store=InMemoryArticleStore())).search("some")
    assert result.items[0].slug is None


def test_list_by_glossary_slug_filters_and_sorts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Queries the glossary_slugs field (not term text), newest first -- the "referenced in" list on a glossary term page."""

    class _FakeDocuments:
        def search(self, params: dict) -> dict:
            assert params["filter_by"] == "glossary_slugs:=arc-27"
            assert params["sort_by"] == "published_at:desc"
            return {
                "hits": [
                    {
                        "document": {
                            "id": "a1",
                            "title": "Wallets adopt ARC-27",
                            "summary": "Summary",
                            "service_id": "svc",
                            "published_at": 1700000000,
                        }
                    }
                ]
            }

    class _FakeCollection:
        documents = _FakeDocuments()

    class _FakeCollections:
        def __getitem__(self, _name: str) -> _FakeCollection:
            return _FakeCollection()

    class _FakeClient:
        collections = _FakeCollections()

    monkeypatch.setattr(
        "app.modules.search.services.search_service.get_typesense_client",
        lambda: _FakeClient(),
    )
    monkeypatch.setattr(
        "app.modules.search.services.search_service.ensure_articles_collection",
        lambda: True,
    )
    items = SearchService(
        news_service=NewsService(store=InMemoryArticleStore())
    ).list_by_glossary_slug("arc-27")
    assert len(items) == 1
    assert items[0].article_id == "a1"
    assert items[0].title == "Wallets adopt ARC-27"


def test_list_by_glossary_slug_empty_when_typesense_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No feed-scan fallback for the cross-reference list -- unavailable Typesense means an empty (not erroring) list."""
    monkeypatch.setattr(
        "app.modules.search.services.search_service.get_typesense_client",
        lambda: None,
    )
    items = SearchService(
        news_service=NewsService(store=InMemoryArticleStore())
    ).list_by_glossary_slug("arc-27")
    assert items == []


def test_list_by_glossary_slug_blank_slug_short_circuits() -> None:
    """A blank/whitespace-only slug returns empty without touching Typesense at all."""
    items = SearchService(
        news_service=NewsService(store=InMemoryArticleStore())
    ).list_by_glossary_slug("  ")
    assert items == []
