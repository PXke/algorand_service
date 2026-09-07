"""Read-through service over the news and search modules for the x402 News Engine.

Owns no storage and no query text: every read is a call into NewsService or
SearchService, which are the same objects the public site reads through. What
this layer adds is the wire shape an agent pays for -- absolute public URLs,
the translation-language list, and bounded result sizes.
"""

from __future__ import annotations

import logging

from app.core.cache import cached_json
from app.core.config import settings
from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.news.services.news_service import NewsService
from app.modules.search.services.search_service import SearchService
from app.modules.seo.render import article_url

logger = logging.getLogger(__name__)


class NewsEngineService:
    """Headline list, tag discovery, gated article detail, and search, in the News Engine's wire shape."""

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

    def list_headlines(
        self, *, limit: int, tag: str | None = None, lang: str | None = None
    ) -> list[dict]:
        """Latest published headlines, newest first, optionally filtered to one tag.

        `lang` overlays each headline's title/summary with the stored
        translation where one exists (the feed rows' lightweight
        `translated_titles` column, migration 087) -- best-effort per item,
        English where a translation is missing, same as the public feed.
        """
        items, _ = self._news.list_feed_page(limit=limit, tag=tag, lang=lang)
        return [self.headline_json(item) for item in items]

    def tag_stats(self, *, limit: int) -> dict:
        """The paper's tag taxonomy: per-tag article count, readership and recency.

        Reads through the same 30-minute cache the public /api/v1/news/tags
        route fills (identical key, identical builder), so the two surfaces
        share one computation: tag_stats() fans out across the whole tag
        universe (see NewsService.tag_stats's own docstring) and must not run
        once per marketplace caller. The cache fails open -- a Redis hiccup
        means a recompute, never an error. Only the bounded slice is served:
        tags come back sorted by coverage (count desc, then views), so the
        slice is the head of the taxonomy, not an arbitrary subset.
        """
        stats = cached_json("news:tags", 1800, self._news.tag_stats)
        return {
            "article_count": stats["article_count"],
            "tags": stats["tags"][:limit],
            "tag_count_total": len(stats["tags"]),
        }

    def clamp_tag_limit(self, requested: int | None) -> int:
        """Bound a caller's tag limit to [1, x402_news_max_tags]; None means the maximum."""
        cap = settings.x402_news_max_tags
        if requested is None:
            return cap
        return max(1, min(requested, cap))

    def resolve_article(self, raw_id: str, *, lang: str | None = None) -> ArticleDetail | None:
        """The published, non-draft article behind an id or slug, or None.

        Drafts and unknown ids both read as None -- NewsService.get_article
        applies the same admin-only draft gate the public JSON API applies.
        `lang` overlays title/summary/body with that stored translation when
        one exists; the article comes back in English otherwise (article_json
        reports which was actually served).
        """
        article_id = self._news.resolve_slug(raw_id) or raw_id
        return self._news.get_article(article_id, lang=lang)

    def translation_langs(self, article_id: str) -> list[str]:
        """Language codes a translation exists for, or [] when that lookup fails.

        This is a second store read, made after the article body has already
        been resolved: a failure here is logged and degrades to an empty list
        rather than turning an already-resolved article into a 500. The
        degraded payload reads as `lang: "en"` with no translations -- never
        a fabricated language claim.
        """
        try:
            return self._news.translation_langs_for(article_id)
        except Exception:
            logger.warning(
                "x402 news: translation lookup failed for article %s; serving without translations",
                article_id,
                exc_info=True,
            )
            return []

    def article_json(self, detail: ArticleDetail, *, lang_requested: str | None = None) -> dict:
        """The full article payload: body markdown plus every source and translation the caller can follow up on.

        `lang` in the payload is the language actually SERVED, not the one
        requested: NewsService._to_detail falls back to English silently when
        the requested translation does not exist, so this recomputes the
        outcome from the stored translation set -- an agent consuming the
        body must never have to guess which language it got.
        """
        sources = [detail.source_url] if detail.source_url else []
        translations = self.translation_langs(detail.article_id)
        lang_served = lang_requested if lang_requested in translations else "en"
        return {
            "lang": lang_served,
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
            "translations_available": translations,
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
