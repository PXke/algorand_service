"""In-memory article store for tests."""

from __future__ import annotations

from app.modules.news.stores.base import StoredArticle


class InMemoryArticleStore:
    """In-memory article store for tests."""

    def __init__(self) -> None:
        """Start with an empty in-process article table and feed list."""
        self._by_id: dict[str, StoredArticle] = {}
        self._feed: list[StoredArticle] = []

    def insert(self, article: StoredArticle, *, feed_bucket: str = "main") -> None:
        """Insert a new article and keep the in-process feed sorted newest first."""
        _ = feed_bucket
        self._by_id[article.article_id] = article
        self._feed.append(article)
        self._feed.sort(key=lambda item: item.published_at_epoch, reverse=True)

    def list_feed(self, *, feed_bucket: str = "main", limit: int = 50) -> list[StoredArticle]:
        """List recent feed rows, newest first."""
        _ = feed_bucket
        return self._feed[:limit]

    def list_feed_page(
        self,
        *,
        limit: int = 50,
        cursor_epoch_ms: int | None = None,
        max_months: int = 18,
    ) -> tuple[list[StoredArticle], int | None]:
        """Keyset pagination mirroring the Cassandra store: newest first, cursor is the published-at of the last returned item (ms)."""
        _ = max_months
        items = self._feed
        if cursor_epoch_ms is not None:
            cursor_epoch = cursor_epoch_ms / 1000
            items = [a for a in items if a.published_at_epoch < cursor_epoch]
        page = items[:limit]
        next_cursor = page[-1].published_at_epoch * 1000 if len(page) >= limit and page else None
        return page, next_cursor

    def get(self, article_id: str) -> StoredArticle | None:
        """Fetch one article by id, or None if it does not exist."""
        return self._by_id.get(article_id)
