from __future__ import annotations

from app.core.config import settings
from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.news.services.trigger_kind import classify_article_trigger
from app.modules.news.stores.base import ArticleStore
from app.modules.news.stores.factory import get_article_store


class NewsService:
    def __init__(self, store: ArticleStore | None = None) -> None:
        self._store = store or get_article_store()

    def count_feed(self, *, feed_bucket: str | None = None) -> int:
        """Approximate feed size (in-memory exact; Cassandra capped query)."""
        bucket = feed_bucket or settings.news_feed_bucket
        articles = self._store.list_feed(feed_bucket=bucket, limit=500)
        return len(articles)

    def list_feed(
        self,
        *,
        limit: int | None = None,
        service_id: str | None = None,
    ) -> list[ArticleFeedItem]:
        items, _ = self.list_feed_page(limit=limit, service_id=service_id)
        return items

    def list_feed_page(
        self,
        *,
        limit: int | None = None,
        service_id: str | None = None,
        cursor_epoch_ms: int | None = None,
    ) -> tuple[list[ArticleFeedItem], int | None]:
        cap = limit if limit is not None else settings.news_feed_limit
        if service_id:
            # Filtered view: over-fetch and filter (no cross-partition cursor).
            articles = self._store.list_feed(limit=max(cap * 4, 100))
            articles = [a for a in articles if a.service_id == service_id][:cap]
            return [self._to_feed_item(a) for a in articles], None
        articles, next_cursor = self._store.list_feed_page(
            limit=cap, cursor_epoch_ms=cursor_epoch_ms
        )
        # Defensive: skip any malformed feed rows (e.g. a partial upsert that
        # left service_id/title null) so one bad row can't 500 the whole feed.
        articles = [a for a in articles if a.service_id and a.title]
        return [self._to_feed_item(a) for a in articles], next_cursor

    def get_article(self, article_id: str) -> ArticleDetail | None:
        article = self._store.get(article_id)
        if article is None:
            return None
        tags = list(article.tags or [])
        from app.modules.news.stores.view_counts import get_views

        return ArticleDetail(
            article_id=article.article_id,
            service_id=article.service_id,
            title=article.title,
            summary=article.summary,
            body=article.body,
            published_at_epoch=article.published_at_epoch,
            trigger_txid=article.trigger_txid,
            trigger_round=article.trigger_round,
            source_url=article.source_url,
            tags=tags,
            trigger_kind=classify_article_trigger(
                service_id=article.service_id,
                trigger_txid=article.trigger_txid,
                trigger_round=article.trigger_round,
                source_url=article.source_url,
                tags=tags,
            ),
            views=get_views(article.article_id),
            image_url=article.image_url,
        )

    @staticmethod
    def _to_feed_item(article) -> ArticleFeedItem:
        tags = list(article.tags or [])
        return ArticleFeedItem(
            article_id=article.article_id,
            service_id=article.service_id,
            title=article.title,
            summary=article.summary,
            published_at_epoch=article.published_at_epoch,
            trigger_txid=article.trigger_txid,
            trigger_round=article.trigger_round,
            tags=tags,
            trigger_kind=classify_article_trigger(
                service_id=article.service_id,
                trigger_txid=article.trigger_txid,
                trigger_round=article.trigger_round,
                tags=tags,
            ),
            image_url=getattr(article, "image_url", None),
            source_url=getattr(article, "source_url", None),
        )
