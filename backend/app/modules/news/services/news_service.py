"""Feed assembly, article reads, and engagement rankings (hot/top)."""

from __future__ import annotations

from collections.abc import Callable

from app.core.cache import cached_json
from app.core.config import settings
from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.news.services.trigger_kind import classify_article_trigger
from app.modules.news.stores.base import ArticleStore, StoredArticle
from app.modules.news.stores.factory import get_article_store


class NewsService:
    """Feed assembly, article reads, and engagement rankings (hot/top)."""

    def __init__(self, store: ArticleStore | None = None) -> None:
        """Wire the article store, defaulting to the configured backend."""
        self._store = store or get_article_store()

    def count_feed(self, *, feed_bucket: str | None = None) -> int:
        """Approximate feed size (in-memory exact; Cassandra capped query).

        Cached because the only callers are display counters — the "Feed
        articles" tile and /news/stats — while the query itself is expensive
        out of proportion to a `len()`: it walks up to 18 month partitions and
        materialises 500 StoredArticle rows, each carrying the 8-language
        `translations` map. Until 2026-07-27 the Cassandra store spelled the
        keyword `_feed_bucket`, so this raised TypeError and the tile silently
        rendered "—"; fixing that name put ~1 MB of Cassandra traffic on an
        endpoint every open tab polls once a minute. A minutes-stale count is
        indistinguishable to a reader.
        """
        bucket = feed_bucket or settings.news_feed_bucket
        return cached_json(
            f"news:feed-count:{bucket}",
            300,
            lambda: len(self._store.list_feed(feed_bucket=bucket, limit=500)),
        )

    def list_feed(
        self,
        *,
        limit: int | None = None,
        service_id: str | None = None,
        lang: str | None = None,
    ) -> list[ArticleFeedItem]:
        """List feed items for the front page (wraps list_feed_page, discarding the cursor)."""
        items, _ = self.list_feed_page(limit=limit, service_id=service_id, lang=lang)
        return items

    def list_feed_page(
        self,
        *,
        limit: int | None = None,
        service_id: str | None = None,
        tag: str | None = None,
        cursor_epoch_ms: int | None = None,
        lang: str | None = None,
    ) -> tuple[list[ArticleFeedItem], int | None]:
        """List a page of feed items, keyset-paginated by cursor_epoch_ms."""
        cap = limit if limit is not None else settings.news_feed_limit
        if service_id or tag:
            # Filtered view: over-fetch and filter (no cross-partition cursor).
            articles = self._store.list_feed(limit=max(cap * 4, 100))
            if service_id:
                articles = [a for a in articles if a.service_id == service_id]
            if tag:
                wanted = tag.strip().lower()
                articles = [
                    a for a in articles if any(t.strip().lower() == wanted for t in (a.tags or []))
                ]
            articles = articles[:cap]
            return (
                self._with_feed_views([self._to_feed_item(a, lang) for a in articles]),
                None,
            )
        articles, next_cursor = self._store.list_feed_page(
            limit=cap, cursor_epoch_ms=cursor_epoch_ms
        )
        # Defensive: skip any malformed feed rows (e.g. a partial upsert that
        # left service_id/title null) so one bad row can't 500 the whole feed.
        articles = [a for a in articles if a.service_id and a.title]
        return self._with_feed_views([self._to_feed_item(a, lang) for a in articles]), next_cursor

    def _with_feed_views(self, items: list[ArticleFeedItem]) -> list[ArticleFeedItem]:
        """Attach lifetime read tallies to feed items (best-effort)."""
        if not items:
            return items
        try:
            from app.modules.news.stores.view_counts import get_views_bulk

            views = get_views_bulk([i.article_id for i in items])
            for item in items:
                item.views = views.get(item.article_id, 0)
        except Exception:
            pass
        return items

    # ── Engagement views (tag cloud + most-read) ─────────────────────────────
    #
    # Both scan the recent feed (bounded) and join the per-article read
    # counters. At current corpus size this is a few hundred rows; callers
    # cache the result (see routes) so the scan runs at most every few minutes.

    _ENGAGEMENT_SCAN_LIMIT = 500

    def _recent_with_views(self, lang: str | None = None) -> list[tuple[ArticleFeedItem, int]]:
        from app.modules.news.stores.view_counts import get_views_bulk

        articles = self._store.list_feed(limit=self._ENGAGEMENT_SCAN_LIMIT)
        articles = [a for a in articles if a.service_id and a.title]
        views = get_views_bulk([a.article_id for a in articles])
        return [(self._to_feed_item(a, lang), views.get(a.article_id, 0)) for a in articles]

    def hot_feed(
        self, *, limit: int = 20, lang: str | None = None, rank: str = "hot"
    ) -> list[ArticleFeedItem]:
        """Reader-engagement ranking over the recent feed.

        rank="hot": read VELOCITY — views divided by days since publication —
        so a fresh story earning reads fast beats an old story coasting on its
        lifetime total (raw-total ranking visibly ossified: the module showed
        the same six mid-June stories for weeks). Age is floored at 6h so a
        just-published story needs real traction, not two lucky clicks.
        rank="top": lifetime totals — the all-time most-read ledger.
        Ties break newest-first either way.
        """
        import time as _time

        now = _time.time()

        def velocity(pair: tuple[ArticleFeedItem, int]) -> float:
            item, views = pair
            # Age from the ORIGINAL publication: a recompose re-publish
            # re-stamps published_at, and lifetime views divided by a
            # just-reset age would catapult any refreshed old article to #1.
            born = item.first_published_at_epoch or item.published_at_epoch
            age_days = max((now - born) / 86400.0, 0.25)
            return views / age_days

        key = (
            (lambda pair: (velocity(pair), pair[0].published_at_epoch))
            if rank == "hot"
            else (lambda pair: (pair[1], pair[0].published_at_epoch))
        )
        ranked = sorted(self._recent_with_views(lang), key=key, reverse=True)
        items: list[ArticleFeedItem] = []
        for item, views in ranked[:limit]:
            item.views = views
            items.append(item)
        return items

    def tag_stats(self) -> dict:
        """Per-tag coverage and readership over the recent feed: how often the newsroom tagged a topic, how many reads those stories drew, and when the topic last appeared. Tags are the writer's own labels, so this is the paper's real taxonomy (richer than the fixed sections)."""
        stats: dict[str, dict] = {}
        pairs = self._recent_with_views()
        for item, views in pairs:
            for raw in item.tags or []:
                tag = raw.strip().lower()
                if not tag:
                    continue
                entry = stats.setdefault(tag, {"tag": tag, "count": 0, "views": 0, "last_epoch": 0})
                entry["count"] += 1
                entry["views"] += views
                entry["last_epoch"] = max(entry["last_epoch"], item.published_at_epoch)
        ordered = sorted(stats.values(), key=lambda e: (-e["count"], -e["views"]))
        return {"article_count": len(pairs), "tags": ordered}

    def translation_langs_for(self, article_id: str) -> list[str]:
        """List the language codes this article has a stored translation for."""
        article = self._store.get(article_id)
        if article is None or not article.translations:
            return []
        return sorted(article.translations.keys())

    def list_feed_for_sitemap(
        self, *, limit: int
    ) -> tuple[list[ArticleFeedItem], dict[str, list[str]]]:
        """Feed rows plus translation language codes for multilingual sitemaps."""
        articles, _ = self._store.list_feed_page(limit=limit)
        articles = [a for a in articles if a.service_id and a.title]
        items = [self._to_feed_item(a) for a in articles]
        translations = {
            a.article_id: sorted(a.translations.keys()) for a in articles if a.translations
        }
        return items, translations

    def resolve_slug(self, slug: str) -> str | None:
        """Article id for a URL slug, or None. Single-partition read on the reverse index."""
        return self._store.id_for_slug(slug)

    def get_article(
        self,
        article_id: str,
        lang: str | None = None,
        *,
        overlap: Callable[[], object] | None = None,
    ) -> ArticleDetail | None:
        """Fetch one article's full detail, translated if lang is given and available.

        ``overlap`` runs while the Cassandra article fetch is in flight (no-op for
        the in-memory store). View counts are fetched in parallel with the article
        body when Cassandra is in use.
        """
        from uuid import UUID

        view_future = None
        try:
            if settings.news_store.strip().lower() == "cassandra":
                from app.core.cassandra import execute_async
                from app.core.statements import ViewCountStmts

                view_future = execute_async(ViewCountStmts.GET, (UUID(article_id),))
        except Exception:
            view_future = None

        def _overlap() -> None:
            if overlap is not None:
                overlap()

        get = self._store.get
        try:
            article = get(article_id, overlap=_overlap)  # type: ignore[call-arg]
        except TypeError:
            _overlap()
            article = get(article_id)
        if article is None:
            if view_future is not None:
                try:
                    view_future.result()
                except Exception:
                    pass
            return None

        views = 0
        if view_future is not None:
            try:
                row = view_future.result().one()
                if row is not None and row.views is not None:
                    views = int(row.views)
            except Exception:
                views = 0
        else:
            from app.modules.news.stores.view_counts import get_views

            views = get_views(article.article_id)

        return self._to_detail(article, lang=lang, views=views)

    def get_articles(
        self, article_ids: list[str], lang: str | None = None
    ) -> dict[str, ArticleDetail]:
        """Fetch many article details concurrently (RSS / bulk enrichment)."""
        getter = getattr(self._store, "get_many", None)
        if getter is None:
            stored = {
                aid: article
                for aid in article_ids
                if (article := self._store.get(aid)) is not None
            }
        else:
            stored = getter(article_ids)

        views: dict[str, int] = {}
        try:
            from app.modules.news.stores.view_counts import get_views_bulk

            views = get_views_bulk(list(stored.keys()))
        except Exception:
            views = {}

        return {
            aid: self._to_detail(article, lang=lang, views=views.get(aid, 0))
            for aid, article in stored.items()
        }

    def _to_detail(
        self, article: StoredArticle, *, lang: str | None = None, views: int = 0
    ) -> ArticleDetail:
        title = article.title
        summary = article.summary
        body = article.body

        if lang and article.translations and lang in article.translations:
            import json

            try:
                t = json.loads(article.translations[lang])
                if t.get("title"):
                    title = t["title"]
                if t.get("summary"):
                    summary = t["summary"]
                if t.get("body"):
                    body = t["body"]
            except Exception:
                pass

        tags = list(article.tags or [])
        return ArticleDetail(
            article_id=article.article_id,
            service_id=article.service_id,
            title=title,
            summary=summary,
            body=body,
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
            views=views,
            image_url=article.image_url,
            slug=getattr(article, "slug", None),
            updated_at_epoch=getattr(article, "updated_at_epoch", None),
        )

    @staticmethod
    def _to_feed_item(article: StoredArticle, lang: str | None = None) -> ArticleFeedItem:
        tags = list(article.tags or [])

        title = article.title
        summary = article.summary
        if lang and getattr(article, "translations", None) and lang in article.translations:
            import json

            try:
                t = json.loads(article.translations[lang])
                if t.get("title"):
                    title = t["title"]
                if t.get("summary"):
                    summary = t["summary"]
            except Exception:
                pass

        return ArticleFeedItem(
            article_id=article.article_id,
            service_id=article.service_id,
            title=title,
            summary=summary,
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
            slug=getattr(article, "slug", None),
            first_published_at_epoch=getattr(article, "first_published_at_epoch", None),
            updated_at_epoch=getattr(article, "updated_at_epoch", None),
        )
