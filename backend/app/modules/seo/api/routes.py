"""Server-rendered document routes + robots/sitemap.

nginx (web vhost) proxies navigation requests for these paths to the backend so
crawlers and social scrapers receive real `<title>`/meta/OG/JSON-LD and a
visible `#ssr-body` content, while humans still boot the Flutter app from the same HTML.
Static assets keep being served from disk by nginx.
"""

from __future__ import annotations

import html
import logging
from uuid import UUID

from robyn import Request, Response

from app.core import serialization
from app.core.article_translation_langs import html_lang_for
from app.core.config import settings
from app.core.http_errors import json_error_response
from app.core.query_params import query_param
from app.modules.news.services.news_service import NewsService
from app.modules.seo import analytics_store, feeds, render, shell, sitemap
from app.modules.seo.markdown import md_to_html
from app.modules.seo.topics import (
    SECTION_REDIRECTS,
    cached_feed_snapshot,
    is_reliable_tag,
    items_for_tag,
)
from app.schemas import PageviewBeaconRequest

logger = logging.getLogger(__name__)

_HOME_LIMIT = 30
_FRONT_HOT_LIMIT = 6
_NEWS_SSR_LIMIT = 30
_SECTION_LIMIT = 30
_FEED_FULL_CONTENT_LIMIT = 20  # newest items carry full content:encoded HTML
_SITEMAP_LIMIT = 5000


from app.core.tracking import tracking_opted_out_from_headers


def _doc_response(
    parts: tuple[str, str],
    cache: str,
    status: int = 200,
    *,
    tracked_path: str | None = None,
    html_lang: str = "en",
) -> Response:
    head, body = parts
    if tracked_path:
        body = shell.ssr_track_snippet(tracked_path) + body
    document = shell.render_document(head, body, html_lang=html_lang)
    if document is None:
        # Shell template not found — still return valid HTML AND boot Flutter
        # (the bootstrap script must be present or the app renders a blank page).
        track = shell.ssr_track_snippet(tracked_path) if tracked_path else ""
        document = (
            f'<!DOCTYPE html><html lang="{html.escape(html_lang, quote=True)}"><head><base href="/">'
            f"{head}</head><body>{track}{body}"
            '<script src="/flutter_bootstrap.js" async></script>'
            "</body></html>"
        )
    return Response(
        status_code=status,
        headers={"Content-Type": "text/html; charset=utf-8", "Cache-Control": cache},
        description=document,
    )


def _header(request: Request, name: str) -> str:
    h = request.headers
    return h.get(name) or h.get(name.lower()) or h.get(name.title()) or ""


def _query_params(request: Request) -> dict:
    """Best-effort dict of the request's query params (Robyn shapes vary)."""
    qp = getattr(request, "query_params", None)
    if qp is None:
        return {}
    for attr in ("to_dict", "queries"):
        fn = getattr(qp, attr, None)
        if callable(fn):
            try:
                return dict(fn())
            except Exception:
                logger.debug("query_params.%s() shape unusable", attr, exc_info=True)
    try:
        return dict(qp)
    except Exception:
        return {}


def _record(request: Request, path: str) -> None:
    """Best-effort pageview record for a public document route."""
    if tracking_opted_out_from_headers(request.headers):
        return
    analytics_store.record_pageview(
        path=path,
        referer=_header(request, "referer") or _header(request, "referrer"),
        user_agent=_header(request, "user-agent"),
        # Behind nginx the real client is in X-Forwarded-For / X-Real-IP.
        client_ip=_header(request, "x-forwarded-for") or _header(request, "x-real-ip"),
        # Campaign tag (utm_*/ref) off the landing URL — names dark-social traffic.
        campaign=analytics_store.campaign_label(_query_params(request)),
        accept_language=_header(request, "accept-language"),
        # Sent by every evergreen browser on both the initial document GET and
        # this same beacon POST — see analytics_store.is_missing_fetch_metadata.
        sec_fetch_mode=_header(request, "sec-fetch-mode"),
    )


_BEACON_STATIC_PATHS = {
    "/",
    "/news",
    "/hot",
    "/topics",
    "/about",
    "/contact",
    "/search",
    "/suggestions",
}


def _is_known_app_path(path: str) -> bool:
    """True for a path the Flutter router can actually land on — keeps the
    unauthenticated beacon from letting a client bump counters for arbitrary
    made-up paths (cardinality/data-quality, not just a hard filter)."""
    if path in _BEACON_STATIC_PATHS:
        return True
    if path.startswith("/news/articles/"):
        try:
            UUID(path[len("/news/articles/") :])
        except ValueError:
            return False
        return True
    if path.startswith("/topic/"):
        # Any non-empty slug: the Flutter router serves every /topic/:tag.
        # Cap length to keep junk out of the analytics store.
        slug = path[len("/topic/") :]
        return 0 < len(slug) <= 48 and "/" not in slug
    return False


def _article_tombstoned(article_id: str) -> bool:
    """Was this article deliberately deleted (vs never existed)? Fail-open to
    False — a lookup error must degrade to the plain 404, never break SSR."""
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import DeletedArticleStmts

        aid = UUID(article_id)
        row = get_cassandra_session().execute(DeletedArticleStmts.GET, (aid,)).one()
        return row is not None
    except Exception:
        return False


def _record_notfound(request: Request, path: str) -> None:
    """Best-effort record of a request to an unknown article/section URL."""
    analytics_store.record_notfound(
        path=path,
        client_ip=_header(request, "x-forwarded-for") or _header(request, "x-real-ip"),
    )


def _text_response(body: str, content_type: str, cache: str) -> Response:
    return Response(
        status_code=200,
        headers={"Content-Type": content_type, "Cache-Control": cache},
        description=body,
    )


def _response_for_head(response: Response) -> Response:
    """Robyn does not auto-register HEAD for GET routes; crawlers (Yandex
    sitemap analyzer, etc.) probe with HEAD and treat non-200 as failure."""
    headers = dict(response.headers) if response.headers else {}
    return Response(
        status_code=response.status_code,
        headers=headers,
        description="",
    )


def _mirror_head(app, path: str, get_handler) -> None:
    """Register HEAD on `path` with the same status/headers as GET, no body."""

    @app.head(path)
    async def _head(request: Request) -> Response:
        return _response_for_head(await get_handler(request))


def register_seo_routes(app) -> None:
    news = NewsService()

    @app.get("/")
    async def home(request: Request) -> Response:
        path = "/"
        _record(request, path)
        items = news.list_feed(limit=_HOME_LIMIT)
        hot = news.hot_feed(limit=_FRONT_HOT_LIMIT)
        feed, topics = cached_feed_snapshot(news.list_feed)
        _ = feed
        return _doc_response(
            render.render_front(items, hot, topic_links=topics),
            "public, max-age=120",
            tracked_path=path,
        )

    @app.get("/news")
    async def news_index(request: Request) -> Response:
        path = "/news"
        _record(request, path)
        feed, topics = cached_feed_snapshot(news.list_feed)
        items = feed[:_NEWS_SSR_LIMIT]
        return _doc_response(
            render.render_news_feed(items, topic_links=topics, total_count=len(feed)),
            "public, max-age=120",
            tracked_path=path,
        )

    @app.get("/news/articles/:article_id")
    async def article(request: Request) -> Response:
        article_id = request.path_params.get("article_id", "")
        qp = _query_params(request)
        lang = query_param(qp.get("lang")) or None
        if lang == "en":
            lang = None
        detail = news.get_article(article_id, lang=lang) if article_id else None
        if detail is None:
            _record_notfound(request, f"/news/articles/{article_id}")
            # 410 Gone for tombstoned (deliberately deleted) articles: their
            # URLs live on in old sitemaps/crawl queues, and Google drops a 410
            # promptly instead of re-trying a 404 for months. Fail-open to 404
            # when the tombstone lookup itself errors.
            if _article_tombstoned(article_id):
                return _doc_response(
                    render.render_noindex("Article removed"),
                    "public, max-age=86400",
                    status=410,
                )
            return _doc_response(
                render.render_noindex("Article not found"), "public, max-age=60", status=404
            )
        translation_langs = news.translation_langs_for(article_id)
        path = f"/news/articles/{article_id}"
        _record(request, path)
        # Footer topic links + related stories reuse the cached topics-index feed.
        feed, topics = cached_feed_snapshot(news.list_feed)
        related = render.pick_related_articles(detail, feed, limit=5)
        return _doc_response(
            render.render_article(
                detail,
                lang=lang,
                translation_langs=translation_langs,
                topic_links=topics,
                related=related,
            ),
            "public, max-age=300, stale-while-revalidate=600",
            tracked_path=path,
            html_lang=html_lang_for(lang),
        )

    @app.get("/og/article/:article_id")
    async def og_article_card(request: Request) -> Response:
        """Generated share-card PNG (accent slug, kicker, serif headline —
        see seo/share_card.py) for an article's title/primary tag. Always
        generates regardless of whether the article has a real photo; the
        DECISION to use this vs. a real og:image lives in render.py, which is
        the only caller that should ever link here."""
        import asyncio
        import hashlib

        from app.core.cache import cached_bytes
        from app.modules.seo.topics import display_tag_label, primary_tag

        article_id = request.path_params.get("article_id", "")
        if article_id.endswith(".png"):
            article_id = article_id[: -len(".png")]
        detail = news.get_article(article_id) if article_id else None
        if detail is None:
            return Response(
                status_code=404,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                description="Not found",
            )
        raw_tag = primary_tag(detail.tags)
        kicker = display_tag_label(raw_tag) if raw_tag else ""
        # Deterministic across processes (unlike builtin hash(), which is
        # PYTHONHASHSEED-randomized per run) — title/kicker baked into the
        # key so an edit or recompose auto-busts the cache, no invalidation
        # call needed; the stale entry just ages out of Redis unread.
        digest = hashlib.sha256(f"{detail.title}\x00{kicker}".encode()).hexdigest()[:16]
        cache_key = f"ogcard:{article_id}:{digest}"

        def compute() -> bytes:
            from app.modules.seo.share_card import render_share_card

            return render_share_card(title=detail.title, kicker=kicker)

        data = await asyncio.to_thread(cached_bytes, cache_key, 2_592_000, compute)
        return Response(
            status_code=200,
            headers={"Content-Type": "image/png", "Cache-Control": "public, max-age=86400"},
            description=data,
        )

    @app.get("/section/:slug")
    async def section(request: Request) -> Response:
        # The human-defined sections were retired in favour of writer-tag
        # topics; their URLs are Google-indexed, so 301 to the closest topic.
        slug = request.path_params.get("slug", "").strip().lower()
        target = SECTION_REDIRECTS.get(slug)
        location = f"/topic/{target}" if target else "/topics"
        return Response(
            status_code=301,
            headers={"Location": location, "Cache-Control": "public, max-age=86400"},
            description="",
        )

    @app.get("/hot")
    async def hot(request: Request) -> Response:
        path = "/hot"
        _record(request, path)
        items = news.hot_feed(limit=30)
        _feed, topics = cached_feed_snapshot(news.list_feed)
        return _doc_response(
            render.render_hot(items, topic_links=topics),
            "public, max-age=300",
            tracked_path=path,
        )

    @app.get("/topics")
    async def topics(request: Request) -> Response:
        path = "/topics"
        _record(request, path)
        feed, picked = cached_feed_snapshot(news.list_feed)
        return _doc_response(
            render.render_topics(picked), "public, max-age=300", tracked_path=path
        )

    @app.get("/topic/:tag")
    async def topic(request: Request) -> Response:
        tag = request.path_params.get("tag", "").strip().lower()
        feed, topic_list = cached_feed_snapshot(news.list_feed)
        matching = items_for_tag(feed, tag)
        items = matching[:_SECTION_LIMIT]
        if not items:
            _record_notfound(request, f"/topic/{tag}")
            return _doc_response(
                render.render_noindex("Topic not found"), "public, max-age=60", status=404
            )
        path = f"/topic/{tag}"
        _record(request, path)
        head, body = render.render_topic(
            tag, items, topic_links=topic_list, total_count=len(matching)
        )
        # Thin topics (single story) stay reachable but out of the index.
        if not is_reliable_tag(tag, feed):
            head += '\n<meta name="robots" content="noindex, follow">'
        return _doc_response((head, body), "public, max-age=120", tracked_path=path)

    @app.get("/about")
    async def about(request: Request) -> Response:
        path = "/about"
        _record(request, path)
        return _doc_response(render.render_about(), "public, max-age=3600", tracked_path=path)

    @app.get("/contact")
    async def contact(request: Request) -> Response:
        path = "/contact"
        _record(request, path)
        return _doc_response(render.render_contact(), "public, max-age=3600", tracked_path=path)

    @app.get("/search")
    async def search(request: Request) -> Response:
        _ = request
        return _doc_response(render.render_noindex("Search", active="/search"), "public, max-age=300")

    @app.get("/suggestions")
    async def suggestions(request: Request) -> Response:
        _ = request
        return _doc_response(render.render_noindex("Suggestions"), "public, max-age=300")

    @app.get("/admin")
    async def admin(request: Request) -> Response:
        _ = request
        return _doc_response(render.render_noindex("Admin"), "no-store")

    @app.post("/api/v1/analytics/pageview")
    async def beacon_pageview(request: Request) -> Response:
        """Client-side beacon for a Flutter in-app route change — the initial
        document load is already recorded server-side; this covers navigation
        after that, which never hits a document route."""
        if tracking_opted_out_from_headers(request.headers):
            return {"ok": True}
        try:
            payload = serialization.decode(request.body, PageviewBeaconRequest)
        except serialization.DecodeError as exc:
            return json_error_response(400, "invalid_request", str(exc))
        if not _is_known_app_path(payload.path):
            return json_error_response(400, "invalid_request", "unknown path")
        _record(request, payload.path)
        return {"ok": True}

    @app.get("/robots.txt")
    async def robots(request: Request) -> Response:
        _ = request
        return _text_response(
            sitemap.robots_txt(), "text/plain; charset=utf-8", "public, max-age=3600"
        )

    @app.get("/feed.xml")
    async def rss_feed(request: Request) -> Response:
        _ = request
        items = news.list_feed(limit=50)
        # Full article HTML for the newest items only: readers and AI crawlers
        # get whole pieces, without 50 point-reads on every feed render (the
        # response is cached 15 min anyway).
        bodies: dict[str, str] = {}
        for item in items[:_FEED_FULL_CONTENT_LIMIT]:
            try:
                detail = news.get_article(item.article_id)
                if detail is not None and detail.body:
                    bodies[item.article_id] = md_to_html(detail.body)
            except Exception:  # a missing body never breaks the feed
                continue
        return _text_response(
            feeds.rss_xml(items, bodies=bodies),
            "application/rss+xml; charset=utf-8",
            "public, max-age=900",
        )

    @app.get("/feed/topic/:tag")
    async def topic_rss_feed(request: Request) -> Response:
        tag = request.path_params.get("tag", "").strip().lower()
        if tag.endswith(".xml"):
            tag = tag[: -len(".xml")]
        if not tag or "/" in tag or len(tag) > 48:
            return Response(
                status_code=404,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                description="Not found",
            )
        feed, _ = cached_feed_snapshot(news.list_feed)
        items = items_for_tag(feed, tag)
        if not items:
            return Response(
                status_code=404,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                description="Not found",
            )
        return _text_response(
            feeds.topic_rss_xml(tag, items),
            "application/rss+xml; charset=utf-8",
            "public, max-age=900",
        )

    @app.get("/llms.txt")
    async def llms_txt(request: Request) -> Response:
        _ = request
        return _text_response(
            sitemap.llms_txt(), "text/plain; charset=utf-8", "public, max-age=3600"
        )

    @app.get("/sitemap.xml")
    async def sitemap_root(request: Request) -> Response:
        _ = request
        items, translations = news.list_feed_for_sitemap(limit=_SITEMAP_LIMIT)
        build = sitemap.build_sitemaps(items, translations)
        return _text_response(
            build.root_xml, "application/xml; charset=utf-8", "public, max-age=900"
        )

    @app.get("/sitemap-pages.xml")
    async def sitemap_pages(request: Request) -> Response:
        _ = request
        items, translations = news.list_feed_for_sitemap(limit=_SITEMAP_LIMIT)
        build = sitemap.build_sitemaps(items, translations)
        xml = build.parts.get("sitemap-pages.xml")
        if xml is None:
            return Response(
                status_code=404,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                description="Not found",
            )
        return _text_response(xml, "application/xml; charset=utf-8", "public, max-age=900")

    @app.get("/sitemap-articles-:part")
    async def sitemap_articles_part(request: Request) -> Response:
        _ = request
        part = request.path_params.get("part", "")
        if part.endswith(".xml"):
            part = part[: -len(".xml")]
        try:
            chunk = int(part)
        except ValueError:
            return Response(
                status_code=404,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                description="Not found",
            )
        if chunk < 1:
            return Response(
                status_code=404,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                description="Not found",
            )
        items, translations = news.list_feed_for_sitemap(limit=_SITEMAP_LIMIT)
        build = sitemap.build_sitemaps(items, translations)
        xml = build.parts.get(f"sitemap-articles-{chunk}.xml")
        if xml is None:
            return Response(
                status_code=404,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                description="Not found",
            )
        return _text_response(xml, "application/xml; charset=utf-8", "public, max-age=900")

    @app.get("/sitemap-news.xml")
    async def sitemap_news(request: Request) -> Response:
        _ = request
        # Off until accepted into Google News Publisher Center (see config).
        if not settings.seo_news_sitemap_enabled:
            return Response(
                status_code=404,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                description="Not found",
            )
        items = news.list_feed(limit=_SITEMAP_LIMIT)
        return _text_response(
            sitemap.news_sitemap_xml(items),
            "application/xml; charset=utf-8",
            "public, max-age=900",
        )

    # Mirror HEAD for every GET document/feed route (see _response_for_head).
    for path, handler in (
        ("/", home),
        ("/news", news_index),
        ("/news/articles/:article_id", article),
        ("/og/article/:article_id", og_article_card),
        ("/section/:slug", section),
        ("/hot", hot),
        ("/topics", topics),
        ("/topic/:tag", topic),
        ("/about", about),
        ("/contact", contact),
        ("/search", search),
        ("/suggestions", suggestions),
        ("/admin", admin),
        ("/robots.txt", robots),
        ("/feed.xml", rss_feed),
        ("/feed/topic/:tag", topic_rss_feed),
        ("/llms.txt", llms_txt),
        ("/sitemap.xml", sitemap_root),
        ("/sitemap-pages.xml", sitemap_pages),
        ("/sitemap-articles-:part", sitemap_articles_part),
        ("/sitemap-news.xml", sitemap_news),
    ):
        _mirror_head(app, path, handler)
