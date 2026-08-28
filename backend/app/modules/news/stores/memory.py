"""In-memory article store for tests."""

from __future__ import annotations

from app.modules.news.stores.base import StoredArticle, TagSummary


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

    def id_for_slug(self, slug: str) -> str | None:
        """Article id owning this permanent URL slug, or None."""
        clean = (slug or "").strip().lower()
        return next(
            (a.article_id for a in self._by_id.values() if (a.slug or "").lower() == clean),
            None,
        )

    def get(self, article_id: str) -> StoredArticle | None:
        """Fetch one article by id, or None if it does not exist."""
        return self._by_id.get(article_id)

    def get_detail(self, article_id: str, *, lang: str | None = None) -> StoredArticle | None:
        """Fetch one article for the detail read path -- mirrors CassandraArticleStore.get_detail's (article_id, lang) contract. The in-memory store already holds the complete StoredArticle (including the full translations map) in memory, so there's no lighter projection to make; `lang` is accepted only for interface parity with the caller (NewsService._fetch_detail), which always passes it by keyword."""
        _ = lang
        return self._by_id.get(article_id)

    def get_many(self, article_ids: list[str]) -> dict[str, StoredArticle]:
        """Fetch many articles by id; missing ids are omitted."""
        return {
            aid: article
            for aid in article_ids
            if (article := self._by_id.get(aid)) is not None
        }

    def get_many_detail(
        self, article_ids: list[str], *, lang: str | None = None
    ) -> dict[str, StoredArticle]:
        """Fetch many articles for the bulk detail read path -- mirrors get_many exactly; see get_detail's docstring for why lang is a no-op here."""
        _ = lang
        return self.get_many(article_ids)

    def list_by_tag_page(
        self, tag: str, *, limit: int = 50, cursor_epoch_ms: int | None = None
    ) -> tuple[list[StoredArticle], int | None]:
        """List stored articles carrying `tag` (case/whitespace-insensitive), newest first, keyset-paginated -- mirrors the Cassandra store's cursor convention over the small in-process list."""
        clean = (tag or "").strip().lower()
        if not clean:
            return [], None
        matches = [
            a for a in self._feed if any((t or "").strip().lower() == clean for t in (a.tags or []))
        ]
        if cursor_epoch_ms is not None:
            cursor_epoch = cursor_epoch_ms / 1000
            matches = [a for a in matches if a.published_at_epoch < cursor_epoch]
        page = matches[:limit]
        next_cursor = page[-1].published_at_epoch * 1000 if len(page) >= limit and page else None
        return page, next_cursor

    def tag_summary(self) -> list[TagSummary]:
        """Per-tag (count, last_epoch, article ids) over every stored article -- a plain scan, fine at test/dev scale."""
        stats: dict[str, TagSummary] = {}
        for article in self._feed:
            for raw in article.tags or []:
                tag = (raw or "").strip().lower()
                if not tag:
                    continue
                summary = stats.setdefault(tag, TagSummary(tag=tag))
                summary.count += 1
                summary.last_epoch = max(summary.last_epoch, article.published_at_epoch)
                summary.article_ids.append(article.article_id)
        return list(stats.values())
