"""Server-rendered document routes + robots/sitemap.

nginx (web vhost) proxies navigation requests for these paths to the backend so
crawlers and social scrapers receive real `<title>`/meta/OG/JSON-LD and a
visible `#ssr-body` content, while humans still boot the Vite SPA from the same HTML.
Static assets keep being served from disk by nginx.
"""

from __future__ import annotations

import html
import inspect
import logging
import re
from collections.abc import Awaitable, Callable
from uuid import UUID

from robyn import Request, Response, Robyn

from app.core import serialization
from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS, html_lang_for
from app.core.config import settings
from app.core.http_errors import json_error_response
from app.core.query_params import query_param
from app.core.tracking import tracking_opted_out_from_headers
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


def _doc_response(
    parts: tuple[str, str],
    cache: str,
    status: int = 200,
    *,
    tracked_path: str | None = None,
    dedup_path: str | None = None,
    html_lang: str = "en",
) -> Response:
    """``tracked_path`` is what gets counted server-side (must be the CANONICAL path for an article — see _article_document). ``dedup_path`` is what the client-side beacon dedup marker gets, and must match ``window.location.pathname`` exactly, or the SPA boot's mismatch check fails open and fires a second, redundant beacon for the same load. These differ for a locale-prefixed article: canonical for counting, `/fr/...` for what the browser is actually at. Defaults to ``tracked_path`` for every other route, where the two are the same string."""
    head, body = parts
    if tracked_path:
        body = shell.ssr_track_snippet(dedup_path or tracked_path) + body
    document = shell.render_document(head, body, html_lang=html_lang)
    if document is None:
        # Shell template not found (missing/So far unbuilt frontend). Still return
        # valid crawlable HTML so meta/OG/JSON-LD survive; the SPA can't boot from
        # here because Vite's entry script is content-hashed, so humans get the
        # SSR body until a real build lands.
        track = shell.ssr_track_snippet(dedup_path or tracked_path) if tracked_path else ""
        document = (
            f'<!DOCTYPE html><html lang="{html.escape(html_lang, quote=True)}"><head><base href="/">'
            f"{head}</head><body>{track}{body}"
            '<div id="app"></div>'
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


def _record(request: Request, path: str, *, navigation: bool = True) -> None:
    """Best-effort pageview record. `navigation` is False for the SPA's JSON beacon, whose Accept header legitimately differs from a document request's — see record_pageview."""
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
        # See analytics_store.is_missing_accept_header — only meaningful on a
        # document request, hence `navigation`.
        accept=_header(request, "accept"),
        navigation=navigation,
    )


_BEACON_STATIC_PATHS = {
    "/",
    "/news",
    "/hot",
    "/top",
    "/topics",
    "/about",
    "/contact",
    "/search",
    "/suggestions",
}


# Lowercase alphanumerics and single dashes — the exact shape slugify emits.
_SLUG_SHAPE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _is_known_app_path(path: str) -> bool:
    """True for a path the SPA router can actually land on — keeps the unauthenticated beacon from letting a client bump counters for arbitrary made-up paths (cardinality/data-quality, not just a hard filter)."""
    if path in _BEACON_STATIC_PATHS:
        return True
    if path.startswith("/news/articles/"):
        # A uuid OR a slug (migration 056). Article URLs became slugs on
        # 2026-07-28 and this still demanded a uuid, so every article pageview
        # was rejected with 400 and silently stopped being counted. Slugs are
        # shape-checked rather than looked up: the beacon is unauthenticated
        # and must not become a way to probe which articles exist, and the
        # point of this guard is cardinality control, not authorisation.
        ident = path[len("/news/articles/") :]
        try:
            UUID(ident)
        except ValueError:
            return bool(ident) and len(ident) <= 80 and _SLUG_SHAPE.fullmatch(ident) is not None
        return True
    if path.startswith("/topic/"):
        # Any non-empty slug: the SPA router serves every /topic/:tag.
        # Cap length to keep junk out of the analytics store.
        slug = path[len("/topic/") :]
        return 0 < len(slug) <= 48 and "/" not in slug
    return False


def _article_tombstoned(article_id: str) -> bool:
    """Was this article deliberately deleted (vs never existed)? Shared with the JSON article endpoint — see news.stores.tombstones."""
    from app.modules.news.stores.tombstones import is_article_tombstoned

    return is_article_tombstoned(article_id)


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
    """Robyn does not auto-register HEAD for GET routes; crawlers (Yandex sitemap analyzer, etc.) probe with HEAD and treat non-200 as failure."""
    headers = dict(response.headers) if response.headers else {}
    return Response(
        status_code=response.status_code,
        headers=headers,
        description="",
    )


def _mirror_head(
    app: Robyn,
    path: str,
    get_handler: Callable[[Request], Response | Awaitable[Response]],
) -> None:
    """Register HEAD on `path` with the same status/headers as GET, no body."""

    @app.head(path)
    async def _head(request: Request) -> Response:
        # The GET handlers are plain `def` (Robyn runs those in a worker thread,
        # which is what keeps their blocking queries off the event loop). Accept
        # either shape so this keeps working whichever way a handler is declared
        # -- awaiting a plain Response would raise, and nothing in the test suite
        # calls handlers directly, so that break would only show up in prod.
        result = get_handler(request)
        if inspect.isawaitable(result):
            result = await result
        return _response_for_head(result)


news = NewsService()


def home(request: Request) -> Response:
    """SSR front page: latest feed items plus the hot-reads rail."""
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


def news_index(request: Request) -> Response:
    """SSR news index: the full recent feed."""
    path = "/news"
    _record(request, path)
    feed, topics = cached_feed_snapshot(news.list_feed)
    items = feed[:_NEWS_SSR_LIMIT]
    return _doc_response(
        render.render_news_feed(items, topic_links=topics, total_count=len(feed)),
        "public, max-age=120",
        tracked_path=path,
    )


def _permanent_redirect(target: str) -> Response:
    return Response(
        status_code=301,
        headers={"Location": target, "Cache-Control": "public, max-age=86400"},
        description="",
    )


def article_localized(request: Request) -> Response:
    """SSR article under a locale path segment (``/fr/news/articles/slug``).

    An unknown leading segment is not a locale at all, so it must 404 rather
    than silently serve the English article under a junk prefix -- that would
    mint an unbounded set of duplicate URLs for crawlers to find.
    """
    lang = (request.path_params.get("lang", "") or "").strip().lower()
    if lang not in ARTICLE_TRANSLATION_LANGS:
        _record_notfound(request, f"/{lang}")
        return _doc_response(
            render.render_noindex("Page not found"), "public, max-age=60", status=404
        )
    return _article_document(request, lang)


def article(request: Request) -> Response:
    """SSR article at the bare (English) path; a legacy ``?lang=`` 301s to the locale path."""
    qp = _query_params(request)
    lang = query_param(qp.get("lang")) or None
    if lang and lang != "en" and lang in ARTICLE_TRANSLATION_LANGS:
        # Legacy URL form (indexed until 2026-07-29). Resolve the slug here so
        # this is a SINGLE hop to the final URL — chaining id->slug->locale
        # redirects bleeds crawl budget and dilutes the signal.
        raw = request.path_params.get("article_id", "")
        article_id = news.resolve_slug(raw) or raw
        detail = news.get_article(article_id, lang=lang) if article_id else None
        slug = detail.slug if detail is not None else None
        return _permanent_redirect(render.article_path(article_id, slug or raw, lang))
    return _article_document(request, None)


def _article_document(request: Request, lang: str | None) -> Response:
    """Render one article document for a resolved locale (None = English)."""
    raw = request.path_params.get("article_id", "")

    # The path segment is a slug for every article since migration 056, but old
    # uuid URLs are indexed and must keep working. Try the slug index first; a
    # uuid that resolves gets a permanent redirect to its slug, so search
    # engines consolidate on one URL instead of seeing two for one story.
    article_id = news.resolve_slug(raw) or raw
    detail = news.get_article(article_id, lang=lang) if article_id else None
    if detail is not None and detail.slug and raw != detail.slug:
        return _permanent_redirect(render.article_path(article_id, detail.slug, lang))
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
    # Tracked under the CANONICAL (unprefixed) path regardless of locale: these
    # counters drive per-article view counts and Most Read, and keying them by
    # locale path would split one story's readership across nine URLs and rank
    # every article below its true total.
    path = f"/news/articles/{article_id}"
    _record(request, path)
    # First hand of record_view's two-hand check (news/api/routes.py): a real
    # reader's browser always requests this document before their own
    # client-side JSON fetch fires (Article.svelte re-fetches unconditionally
    # on mount). A scraper going straight for the JSON API skips this.
    analytics_store.mark_article_document_served(
        article_id,
        _header(request, "x-forwarded-for") or _header(request, "x-real-ip"),
        _header(request, "user-agent"),
    )
    # The browser's ACTUAL path, locale prefix included -- must match
    # window.location.pathname on boot exactly, or the SPA's dedup check
    # against the canonical `path` above always fails for a translated
    # article, firing a second (redundant, and unresolvable to a title --
    # see _ARTICLE_PREFIX) beacon under the raw locale path on every direct
    # visit. Root-caused 2026-07-30 from an admin-analytics report the day
    # after locale path URLs shipped.
    browser_path = render.article_path(article_id, detail.slug, lang)
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
        dedup_path=browser_path,
        tracked_path=path,
        html_lang=html_lang_for(lang),
    )


async def og_article_card(request: Request) -> Response:
    """Generated share-card PNG (accent slug, kicker, serif headline — see seo/share_card.py) for an article's title/primary tag. Always generates regardless of whether the article has a real photo; the DECISION to use this vs. a real og:image lives in render.py, which is the only caller that should ever link here."""
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


def section(request: Request) -> Response:
    # The human-defined sections were retired in favour of writer-tag
    # topics; their URLs are Google-indexed, so 301 to the closest topic.
    """301-redirect a retired human-defined section to its closest writer-tag topic."""
    slug = request.path_params.get("slug", "").strip().lower()
    target = SECTION_REDIRECTS.get(slug)
    location = f"/topic/{target}" if target else "/topics"
    return Response(
        status_code=301,
        headers={"Location": location, "Cache-Control": "public, max-age=86400"},
        description="",
    )


def hot(request: Request) -> Response:
    """SSR hot/top reader-engagement page."""
    path = "/hot"
    _record(request, path)
    items = news.hot_feed(limit=30)
    _feed, topics = cached_feed_snapshot(news.list_feed)
    return _doc_response(
        render.render_hot(items, topic_links=topics),
        "public, max-age=300",
        tracked_path=path,
    )


def top(request: Request) -> Response:
    """SSR all-time-top reader-engagement page (the SPA's /top view; /hot is the recency-weighted one)."""
    path = "/top"
    _record(request, path)
    items = news.hot_feed(limit=30, rank="top")
    _feed, topics = cached_feed_snapshot(news.list_feed)
    return _doc_response(
        render.render_hot(items, topic_links=topics, canonical_path=path),
        "public, max-age=300",
        tracked_path=path,
    )


def topics(request: Request) -> Response:
    """SSR topics index page."""
    path = "/topics"
    _record(request, path)
    _feed, picked = cached_feed_snapshot(news.list_feed)
    return _doc_response(render.render_topics(picked), "public, max-age=300", tracked_path=path)


def topic(request: Request) -> Response:
    """SSR one topic's article list, noindexed if the topic is too thin to be reliable."""
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
    head, body = render.render_topic(tag, items, topic_links=topic_list, total_count=len(matching))
    # Thin topics (single story) stay reachable but out of the index.
    if not is_reliable_tag(tag, feed):
        head += '\n<meta name="robots" content="noindex, follow">'
    return _doc_response((head, body), "public, max-age=120", tracked_path=path)


def about(request: Request) -> Response:
    """SSR static about page."""
    path = "/about"
    _record(request, path)
    return _doc_response(render.render_about(), "public, max-age=3600", tracked_path=path)


def contact(request: Request) -> Response:
    """SSR static contact page."""
    path = "/contact"
    _record(request, path)
    return _doc_response(render.render_contact(), "public, max-age=3600", tracked_path=path)


def search(request: Request) -> Response:
    """SSR noindex shell for the client-side search page."""
    _ = request
    return _doc_response(render.render_noindex("Search", active="/search"), "public, max-age=300")


def suggestions(request: Request) -> Response:
    """SSR noindex shell for the client-side suggestions page."""
    _ = request
    return _doc_response(render.render_noindex("Suggestions", active="/suggestions"), "public, max-age=300")


def admin(request: Request) -> Response:
    """SSR noindex, no-store shell for the admin dashboard."""
    _ = request
    return _doc_response(render.render_noindex("Admin", active="/admin"), "no-store")


def _beacon_origin_ok(request: Request) -> bool:
    """Reject a beacon POST that carries no (or a foreign) Origin.

    Stronger than any UA heuristic and free of false positives: the Fetch spec
    makes browsers attach Origin to every non-GET/HEAD request, same-origin
    included, so a real reader's beacon always has one — while curl/requests
    send none unless explicitly told. The CORS middleware does not cover this:
    it only rejects a WRONG origin (`if origin and not allowed`), so a request
    with the header simply absent sails through and gets counted as human.

    Skipped when no origins are configured (cors_origins empty = CORS disabled
    for local dev), so this can't lock out a dev setup.
    """
    from app.core.cors import _origin_allowed

    allowed = settings.cors_origins
    if not allowed:
        return True
    origin = _header(request, "origin")
    return bool(origin) and _origin_allowed(origin, allowed)


def beacon_pageview(request: Request) -> Response:
    """Client-side beacon for an SPA in-app route change — the initial document load is already recorded server-side; this covers navigation after that, which never hits a document route."""
    if tracking_opted_out_from_headers(request.headers):
        return {"ok": True}
    try:
        payload = serialization.decode(request.body, PageviewBeaconRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    if not _beacon_origin_ok(request):
        logger.debug("pageview beacon rejected: missing/foreign Origin")
        return json_error_response(400, "invalid_request", "bad origin")
    if not _is_known_app_path(payload.path):
        return json_error_response(400, "invalid_request", "unknown path")
    _record(request, payload.path, navigation=False)
    return {"ok": True}


def robots(request: Request) -> Response:
    """robots.txt, cached briefly."""
    _ = request
    return _text_response(sitemap.robots_txt(), "text/plain; charset=utf-8", "public, max-age=3600")


def rss_feed(request: Request) -> Response:
    """Site-wide RSS feed, with full article HTML for the newest items."""
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


def topic_rss_feed(request: Request) -> Response:
    """Per-topic RSS feed, or 404 for an unknown/malformed tag."""
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


def llms_txt(request: Request) -> Response:
    """llms.txt, cached briefly."""
    _ = request
    return _text_response(sitemap.llms_txt(), "text/plain; charset=utf-8", "public, max-age=3600")


def sitemap_root(request: Request) -> Response:
    """Root sitemap index."""
    _ = request
    items, translations = news.list_feed_for_sitemap(limit=_SITEMAP_LIMIT)
    build = sitemap.build_sitemaps(items, translations)
    return _text_response(build.root_xml, "application/xml; charset=utf-8", "public, max-age=900")


def sitemap_pages(request: Request) -> Response:
    """The static-pages sitemap chunk, or 404 if it doesn't exist."""
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


def sitemap_articles_part(request: Request) -> Response:
    """One numbered article-sitemap chunk, or 404 for an out-of-range/malformed part."""
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


def sitemap_news(request: Request) -> Response:
    """Google News sitemap, 404 unless the site has been accepted into News Publisher Center."""
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


def register_seo_routes(app: Robyn) -> None:
    """Attach the server-rendered SEO document routes (front page, articles, sitemaps) to the app."""
    app.get("/")(home)
    app.get("/news")(news_index)
    app.get("/news/articles/:article_id")(article)
    app.get("/:lang/news/articles/:article_id")(article_localized)
    app.get("/og/article/:article_id")(og_article_card)
    app.get("/section/:slug")(section)
    app.get("/hot")(hot)
    app.get("/top")(top)
    app.get("/topics")(topics)
    app.get("/topic/:tag")(topic)
    app.get("/about")(about)
    app.get("/contact")(contact)
    app.get("/search")(search)
    app.get("/suggestions")(suggestions)
    app.get("/admin")(admin)
    app.post("/api/v1/analytics/pageview")(beacon_pageview)
    app.get("/robots.txt")(robots)
    app.get("/feed.xml")(rss_feed)
    app.get("/feed/topic/:tag")(topic_rss_feed)
    app.get("/llms.txt")(llms_txt)
    app.get("/sitemap.xml")(sitemap_root)
    app.get("/sitemap-pages.xml")(sitemap_pages)
    app.get("/sitemap-articles-:part")(sitemap_articles_part)
    app.get("/sitemap-news.xml")(sitemap_news)

    # Mirror HEAD for every GET document/feed route (see _response_for_head).
    for path, handler in (
        ("/", home),
        ("/news", news_index),
        ("/news/articles/:article_id", article),
        ("/:lang/news/articles/:article_id", article_localized),
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
