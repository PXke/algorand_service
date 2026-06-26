"""robots.txt and XML sitemaps built from the live news feed."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from app.core.config import settings
from app.modules.news.models.schemas import ArticleFeedItem
from app.modules.seo.render import absolute, article_path, site_url
from app.modules.seo.sections import SECTIONS

# Google News only wants articles from roughly the last two days.
_NEWS_WINDOW_SECONDS = 48 * 3600


def robots_txt() -> str:
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Disallow: /search",
        "Disallow: /suggestions",
        "Disallow: /api/",
        "",
        f"Sitemap: {site_url()}/sitemap.xml",
    ]
    if settings.seo_news_sitemap_enabled:
        lines.append(f"Sitemap: {site_url()}/sitemap-news.xml")
    return "\n".join(lines) + "\n"


def _url(loc: str, lastmod: str | None = None, changefreq: str | None = None) -> str:
    parts = [f"<loc>{escape(loc)}</loc>"]
    if lastmod:
        parts.append(f"<lastmod>{lastmod}</lastmod>")
    if changefreq:
        parts.append(f"<changefreq>{changefreq}</changefreq>")
    return "<url>" + "".join(parts) + "</url>"


def _iso_date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).date().isoformat()


def sitemap_xml(items: list[ArticleFeedItem]) -> str:
    newest = max((i.published_at_epoch for i in items), default=int(time.time()))
    urls = [
        _url(site_url() + "/", _iso_date(newest), "hourly"),
        _url(absolute("/about"), changefreq="monthly"),
    ]
    urls += [_url(absolute(f"/section/{s.slug}"), changefreq="daily") for s in SECTIONS]
    urls += [
        _url(absolute(article_path(i.article_id)), _iso_date(i.published_at_epoch))
        for i in items
    ]
    body = "".join(urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</urlset>"
    )


def news_sitemap_xml(items: list[ArticleFeedItem]) -> str:
    cutoff = int(time.time()) - _NEWS_WINDOW_SECONDS
    recent = [i for i in items if i.published_at_epoch >= cutoff]
    entries = []
    for item in recent:
        pub = datetime.fromtimestamp(item.published_at_epoch, tz=UTC).isoformat()
        entries.append(
            "<url>"
            f"<loc>{escape(absolute(article_path(item.article_id)))}</loc>"
            "<news:news>"
            "<news:publication>"
            f"<news:name>{escape(settings.site_name)}</news:name>"
            "<news:language>en</news:language>"
            "</news:publication>"
            f"<news:publication_date>{pub}</news:publication_date>"
            f"<news:title>{escape(item.title)}</news:title>"
            "</news:news>"
            "</url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
        f"{''.join(entries)}</urlset>"
    )
