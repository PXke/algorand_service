"""RSS 2.0 feed built from the news feed — for readers, aggregators and bots."""

from __future__ import annotations

from email.utils import formatdate
from xml.sax.saxutils import escape

from app.core.config import settings
from app.modules.news.models.schemas import ArticleFeedItem
from app.modules.seo.render import absolute, article_path, site_url


def _rfc822(epoch: int) -> str:
    return formatdate(epoch, usegmt=True)


def rss_xml(
    items: list[ArticleFeedItem],
    *,
    limit: int = 50,
    bodies: dict[str, str] | None = None,
) -> str:
    """`bodies` maps article_id -> rendered HTML body; when provided the item
    carries the FULL article as `content:encoded` (readers, aggregators and AI
    crawlers get the whole piece instead of a 280-char teaser)."""
    items = items[:limit]
    self_url = absolute("/feed.xml")
    newest = max((i.published_at_epoch for i in items), default=0)
    entries = []
    for item in items:
        url = absolute(article_path(item.article_id))
        content = (bodies or {}).get(item.article_id, "")
        entries.append(
            "<item>"
            f"<title>{escape(item.title)}</title>"
            f"<link>{escape(url)}</link>"
            f'<guid isPermaLink="true">{escape(url)}</guid>'
            f"<pubDate>{_rfc822(item.published_at_epoch)}</pubDate>"
            f"<description>{escape(item.summary or '')}</description>"
            + (f"<content:encoded>{escape(content)}</content:encoded>" if content else "")
            + "".join(f"<category>{escape(t)}</category>" for t in (item.tags or []))
            + "</item>"
        )
    head = (
        f"<title>{escape(settings.site_name)}</title>"
        f"<link>{escape(site_url() + '/')}</link>"
        f"<description>{escape(settings.site_tagline)}</description>"
        "<language>en</language>"
        f'<atom:link href="{escape(self_url)}" rel="self" type="application/rss+xml"/>'
    )
    if newest:
        head += f"<lastBuildDate>{_rfc822(newest)}</lastBuildDate>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        f"<channel>{head}{''.join(entries)}</channel></rss>"
    )
