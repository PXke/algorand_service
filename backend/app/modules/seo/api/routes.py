"""Server-rendered document routes + robots/sitemap.

nginx (web vhost) proxies navigation requests for these paths to the backend so
crawlers and social scrapers receive real `<title>`/meta/OG/JSON-LD and a
`<noscript>` body, while humans still boot the Flutter app from the same HTML.
Static assets keep being served from disk by nginx.
"""

from __future__ import annotations

from robyn import Request, Response

from app.core.config import settings
from app.modules.news.services.news_service import NewsService
from app.modules.seo import analytics_store, feeds, render, shell, sitemap
from app.modules.seo.sections import SECTIONS, matches_section, section_for_slug

_HOME_LIMIT = 30
_SECTION_LIMIT = 30
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
    )


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
        return _text_response(
            feeds.rss_xml(items), "application/rss+xml; charset=utf-8", "public, max-age=900"
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
