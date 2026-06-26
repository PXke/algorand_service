from __future__ import annotations

from app.modules.news.services.news_service import NewsService
from app.modules.news.stores.base import StoredArticle
from app.modules.news.stores.memory import InMemoryArticleStore


def test_news_feed_lists_articles_newest_first() -> None:
    store = InMemoryArticleStore()
    store.insert(
        StoredArticle(
            article_id="a1",
            service_id="svc",
            title="Older",
            summary="s1",
            body="b1",
            published_at_epoch=100,
        )
    )
    store.insert(
        StoredArticle(
            article_id="a2",
            service_id="svc",
            title="Newer",
            summary="s2",
            body="b2",
            published_at_epoch=200,
        )
    )
    service = NewsService(store=store)
    items = service.list_feed()
    assert items[0].title == "Newer"
    assert items[1].title == "Older"


def test_news_feed_filters_by_service_id() -> None:
    store = InMemoryArticleStore()
    store.insert(
        StoredArticle(
            article_id="a1",
            service_id="svc-a",
            title="A",
            summary="s",
            body="b",
            published_at_epoch=100,
        )
    )
    store.insert(
        StoredArticle(
            article_id="a2",
            service_id="svc-b",
            title="B",
            summary="s",
            body="b",
            published_at_epoch=200,
        )
    )
    service = NewsService(store=store)
    items = service.list_feed(service_id="svc-a")
    assert [item.article_id for item in items] == ["a1"]


def test_get_article_detail() -> None:
    store = InMemoryArticleStore()
    store.insert(
        StoredArticle(
            article_id="id-1",
            service_id="svc",
            title="T",
            summary="S",
            body="Full body",
            published_at_epoch=1,
            trigger_txid="T" * 52,
            trigger_round=42,
            source_url="https://example.com",
        )
    )
    detail = NewsService(store=store).get_article("id-1")
    assert detail is not None
    assert detail.body == "Full body"
    assert detail.trigger_round == 42
