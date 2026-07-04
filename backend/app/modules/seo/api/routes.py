"""Server-rendered document routes + robots/sitemap.

nginx (web vhost) proxies navigation requests for these paths to the backend so
crawlers and social scrapers receive real `<title>`/meta/OG/JSON-LD and a
visible `#ssr-body` content, while humans still boot the Flutter app from the same HTML.
Static assets keep being served from disk by nginx.
"""

from __future__ import annotations

import logging
from uuid import UUID

from robyn import Request, Response

from app.core import serialization
from app.core.config import settings
from app.core.http_errors import json_error_response
from app.modules.news.services.news_service import NewsService
from app.modules.seo import analytics_store, feeds, render, shell, sitemap
from app.modules.seo.markdown import md_to_html
from app.modules.seo.sections import SECTIONS, matches_section, section_for_slug
from app.schemas import PageviewBeaconRequest

logger = logging.getLogger(__name__)

_HOME_LIMIT = 12
_SECTION_LIMIT = 30
_FEED_FULL_CONTENT_LIMIT = 20  # newest items carry full content:encoded HTML
_SITEMAP_LIMIT = 500


def _doc_response(parts: tuple[str, str], cache: str, status: int = 200) -> Response:
    head, body = parts
    html = shell.render_document(head, body)
    if html is None:
        # Shell template not found — still return valid HTML AND boot Flutter
        # (the bootstrap script must be present or the app renders a blank page).
        html = (
            '<!DOCTYPE html><html lang="en"><head><base href="/">'
            f"{head}</head><body>{body}"
            '<script src="/flutter_bootstrap.js" async></script>'
            "</body></html>"
        )
    return Response(
        status_code=status,
        headers={"Content-Type": "text/html; charset=utf-8", "Cache-Control": cache},
        description=html,
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
    # Owner opt-out: the app sets this cookie while the admin wallet is connected.
    if "pxke_no_track=1" in _header(request, "cookie"):
        return
    analytics_store.record_pageview(
        path=path,
        referer=_header(request, "referer") or _header(request, "referrer"),
        user_agent=_header(request, "user-agent"),
        # Behind nginx the real client is in X-Forwarded-For / X-Real-IP.
        client_ip=_header(request, "x-forwarded-for") or _header(request, "x-real-ip"),
        # Campaign tag (utm_*/ref) off the landing URL — names dark-social traffic.
        campaign=analytics_store.campaign_label(_query_params(request)),
    )


_BEACON_STATIC_PATHS = {"/", "/news", "/about", "/contact", "/search", "/suggestions"}


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
    if path.startswith("/section/"):
        return section_for_slug(path[len("/section/") :]) is not None
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


def register_seo_routes(app) -> None:
    news = NewsService()

    @app.get("/")
    async def home(request: Request) -> Response:
        _record(request, "/")
        items = news.list_feed(limit=_HOME_LIMIT)
        return _doc_response(render.render_home(items), "public, max-age=120")

    @app.get("/news")
    async def news_index(request: Request) -> Response:
        _record(request, "/news")
        items = news.list_feed(limit=_HOME_LIMIT)
        return _doc_response(render.render_home(items), "public, max-age=120")

    @app.get("/news/articles/:article_id")
    async def article(request: Request) -> Response:
        article_id = request.path_params.get("article_id", "")
        detail = news.get_article(article_id) if article_id else None
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
        _record(request, f"/news/articles/{article_id}")
        return _doc_response(
            render.render_article(detail),
            "public, max-age=300, stale-while-revalidate=600",
        )

    @app.get("/section/:slug")
    async def section(request: Request) -> Response:
        slug = request.path_params.get("slug", "")
        sec = section_for_slug(slug)
        if sec is None:
            _record_notfound(request, f"/section/{slug}")
            return _doc_response(
                render.render_noindex("Section not found"), "public, max-age=60", status=404
            )
        _record(request, f"/section/{slug}")
        feed = news.list_feed(limit=200)
        items = [i for i in feed if matches_section(sec, i.tags)][:_SECTION_LIMIT]
        return _doc_response(render.render_section(sec, items), "public, max-age=120")

    @app.get("/about")
    async def about(request: Request) -> Response:
        _record(request, "/about")
        return _doc_response(render.render_about(), "public, max-age=3600")

    @app.get("/contact")
    async def contact(request: Request) -> Response:
        _record(request, "/contact")
        return _doc_response(render.render_contact(), "public, max-age=3600")

    @app.get("/search")
    async def search(request: Request) -> Response:
        _ = request
        return _doc_response(render.render_noindex("Search"), "public, max-age=300")

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

    @app.get("/llms.txt")
    async def llms_txt(request: Request) -> Response:
        _ = request
        return _text_response(
            sitemap.llms_txt(), "text/plain; charset=utf-8", "public, max-age=3600"
        )

    @app.get("/sitemap.xml")
    async def sitemap_index(request: Request) -> Response:
        _ = request
        items = news.list_feed(limit=_SITEMAP_LIMIT)
        return _text_response(
            sitemap.sitemap_xml(items), "application/xml; charset=utf-8", "public, max-age=900"
        )

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

    _ = SECTIONS  # referenced for completeness; routes resolve sections per-request
