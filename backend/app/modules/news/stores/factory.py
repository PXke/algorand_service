"""Article-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.news.stores.base import ArticleStore
from app.modules.news.stores.cassandra import CassandraArticleStore
from app.modules.news.stores.memory import InMemoryArticleStore

_factory: StoreFactory[ArticleStore] = StoreFactory(
    backend_name=lambda: settings.news_store,
    cassandra=CassandraArticleStore,
    memory=InMemoryArticleStore,
)


def get_article_store() -> ArticleStore:
    """Return the process-wide article store, built from settings on first use."""
    return _factory.get()
