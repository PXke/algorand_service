from __future__ import annotations

from app.modules.news.services.news_service import NewsService
from app.modules.news.stores.base import StoredArticle
from app.modules.news.stores.memory import InMemoryArticleStore
from app.modules.search.services.search_service import SearchService


def test_search_feed_scan_fallback() -> None:
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
