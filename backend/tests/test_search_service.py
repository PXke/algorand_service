from __future__ import annotations

from app.modules.news.services.news_service import NewsService
from app.modules.news.stores.base import StoredArticle
from app.modules.news.stores.memory import InMemoryArticleStore
from app.modules.search.services.search_service import (
    SearchService,
    _feed_article_matches,
    _typesense_num_typos,
    _typesense_prefix_enabled,
)
from app.core.typesense_client import ARTICLES_COLLECTION, expanded_search_terms


def test_search_feed_scan_fallback(monkeypatch) -> None:
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


def test_feed_scan_usa_does_not_match_usability(monkeypatch) -> None:
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


def test_feed_scan_usa_matches_us_via_synonym(monkeypatch) -> None:
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
    terms = expanded_search_terms("USA")
    assert "usa" in terms
    assert "us" in terms


def test_typesense_tuning_for_short_acronyms() -> None:
    assert _typesense_prefix_enabled("USA") is False
    assert _typesense_prefix_enabled("governance") is True
    assert _typesense_num_typos("USA") == 0
    assert _typesense_num_typos("governance") == 2


def test_feed_article_matches_word_boundary() -> None:
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


def test_search_typesense_parses_highlights(monkeypatch) -> None:
    class _FakeDocuments:
        def search(self, params):
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
        def __getitem__(self, _name):
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
    result = SearchService(news_service=NewsService(store=InMemoryArticleStore())).search(
        "USA"
    )
    assert result.engine == "typesense"
    assert len(result.items) == 1
    assert result.items[0].snippet == "the <mark>governance</mark> proposal passed"
