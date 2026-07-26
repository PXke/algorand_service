"""Article-store singleton wiring, swappable for tests."""

from __future__ import annotations

from app.core.config import settings
from app.modules.news.stores.base import ArticleStore
from app.modules.news.stores.cassandra import CassandraArticleStore
from app.modules.news.stores.memory import InMemoryArticleStore

_article_store: ArticleStore | None = None


def get_article_store() -> ArticleStore:
    """Return the process-wide article store, creating it from settings on first use."""
    global _article_store
    if _article_store is None:
        backend = settings.news_store.strip().lower()
        if backend == "cassandra":
            _article_store = CassandraArticleStore()
        else:
            _article_store = InMemoryArticleStore()
    return _article_store


