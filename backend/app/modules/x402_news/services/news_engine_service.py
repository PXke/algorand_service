"""Read-through service over the news and search modules for the x402 News Engine.

Owns no storage and no query text: every read is a call into NewsService or
SearchService, which are the same objects the public site reads through. What
this layer adds is the wire shape an agent pays for -- absolute public URLs,
the translation-language list, and bounded result sizes.
"""

from __future__ import annotations

from app.core.config import settings
from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.news.services.news_service import NewsService
from app.modules.search.services.search_service import SearchService
from app.modules.seo.render import article_url


class NewsEngineService:
    """Headline list, gated article detail, and search, in the News Engine's wire shape."""

    def __init__(
        self,
        news_service: NewsService | None = None,
        search_service: SearchService | None = None,
    ) -> None:
        """Wire the news and search services, defaulting to the configured backends."""
        self._news = news_service or NewsService()
        self._search = search_service or SearchService(self._news)

    def clamp_limit(self, requested: int | None) -> int:
        """Bound a caller's limit to [1, x402_news_max_results]; None means the maximum."""
        cap = settings.x402_news_max_results
        if requested is None:
            return cap
        return max(1, min(requested, cap))

    def list_headlines(self, *, limit: int, tag: str | None = None) -> list[dict]:
        """Latest published headlines, newest first, optionally filtered to one tag."""
        items, _ = self._news.list_feed_page(limit=limit, tag=tag)
        return [self.headline_json(item) for item in items]

    def resolve_article(self, raw_id: str) -> ArticleDetail | None:
        """The published, non-draft article behind an id or slug, or None.

        Drafts and unknown ids both read as None -- NewsService.get_article
        applies the same admin-only draft gate the public JSON API applies.
        """
        article_id = self._news.resolve_slug(raw_id) or raw_id
        return self._news.get_article(article_id)

    def article_json(self, detail: ArticleDetail) -> dict:
        """The full paid article payload: body markdown plus every source and translation the caller can follow up on."""
        sources = [detail.source_url] if detail.source_url else []
        return {
            "article_id": detail.article_id,
            "slug": detail.slug,
            "title": detail.title,
            "summary": detail.summary,
            "body_markdown": detail.body,
            "tags": list(detail.tags),
            "service_id": detail.service_id,
            "trigger_kind": detail.trigger_kind,
            "sources": sources,
            "image_url": detail.image_url,
            "published_at_epoch": detail.published_at_epoch,
            "updated_at_epoch": detail.updated_at_epoch,
            "url": article_url(detail.article_id, slug=detail.slug),
            "translations_available": self._news.translation_langs_for(detail.article_id),
        }

    def search(self, query: str, *, limit: int) -> dict:
        """Ranked article search through the search module; `engine` says which backend answered."""
        result = self._search.search(query, limit=limit)
        return {
            "query": result.query,
            "engine": result.engine,
            "items": [
                {
                    "article_id": hit.article_id,
                    "slug": hit.slug,
                    "title": hit.title,
                    "summary": hit.summary,
                    "snippet": hit.snippet,
                    "score": hit.score,
                    "published_at_epoch": hit.published_at_epoch,
                    "url": article_url(hit.article_id, slug=hit.slug),
                }
                for hit in result.items
            ],
        }

    @staticmethod
    def headline_json(item: ArticleFeedItem) -> dict:
        """The free headline shape: enough to decide whether the full article is worth paying for."""
        return {
            "article_id": item.article_id,
            "slug": item.slug,
            "title": item.title,
            "summary": item.summary,
            "tags": list(item.tags),
            "published_at_epoch": item.published_at_epoch,
            "url": article_url(item.article_id, slug=item.slug),
        }
