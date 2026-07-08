"""robots.txt and XML sitemaps built from the live news feed."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from app.core.config import settings
from app.modules.news.models.schemas import ArticleFeedItem
from app.modules.seo.render import absolute, article_hreflang_links, article_path, site_url
from app.modules.seo.sections import SECTIONS

# Google News only wants articles from roughly the last two days.
_NEWS_WINDOW_SECONDS = 48 * 3600
# Google's hard cap is 50k URLs / 50MB per file; split well before that.
MAX_URLS_PER_SITEMAP = 5000

_URLSET_NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
_XHTML_NS = 'xmlns:xhtml="http://www.w3.org/1999/xhtml"'
_INDEX_NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


@dataclass
class _UrlEntry:
    loc: str
    lastmod: str | None = None
    changefreq: str | None = None
    alternates: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SitemapBuild:
    """One combined urlset, or a sitemap index plus child urlset files."""

    is_index: bool
    root_xml: str
    parts: dict[str, str]


def robots_txt() -> str:
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Disallow: /search",
        "Disallow: /suggestions",
        "",
        f"Sitemap: {site_url()}/sitemap.xml",
    ]
    if settings.seo_news_sitemap_enabled:
        lines.append(f"Sitemap: {site_url()}/sitemap-news.xml")
    return "\n".join(lines) + "\n"


def llms_txt() -> str:
    """llms.txt (llmstxt.org): a markdown site guide for AI crawlers — a real
    audience here (their share of bot traffic is tracked as a first-class
    analytics stat). Points them at the full-content feed and sitemap instead
    of leaving them to scrape the Flutter shell."""
    lines = [
        f"# {settings.site_name}",
        "",
        f"> {settings.site_tagline}",
        "",
        f"{settings.site_name} publishes AI-assisted journalism about the Algorand "
        "ecosystem: on-chain events, market data and community sources under "
        "automated editorial review, with source links on every story.",
        "",
        "## Content",
        "",
        f"- [Latest articles (RSS, full text)]({absolute('/feed.xml')}): every "
        "article's complete body ships in content:encoded",
        f"- [Sitemap]({absolute('/sitemap.xml')}): all article and section URLs "
        "(multilingual; auto-split when large)",
        f"- [About]({absolute('/about')}): editorial and AI-authorship disclosure",
        f"- [Contact]({absolute('/contact')}): corrections, tips and feedback form",
        "",
        "## Sections",
        "",
    ]
    lines += [f"- [{s.label}]({absolute(f'/section/{s.slug}')}): {s.description}" for s in SECTIONS]
    return "\n".join(lines) + "\n"


def _iso_date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).date().isoformat()


def _chunk(entries: list[_UrlEntry], size: int) -> list[list[_UrlEntry]]:
    return [entries[i : i + size] for i in range(0, len(entries), size)]


def _hreflang_link(hreflang: str, href: str) -> str:
    return (
        f'<xhtml:link rel="alternate" hreflang="{escape(hreflang)}" '
        f'href="{escape(href)}"/>'
    )


def _url_xml(entry: _UrlEntry) -> str:
    parts = [f"<loc>{escape(entry.loc)}</loc>"]
    if entry.lastmod:
        parts.append(f"<lastmod>{entry.lastmod}</lastmod>")
    if entry.changefreq:
        parts.append(f"<changefreq>{entry.changefreq}</changefreq>")
    for hreflang, href in entry.alternates:
        parts.append(_hreflang_link(hreflang, href))
    return "<url>" + "".join(parts) + "</url>"


def _urlset_xml(entries: list[_UrlEntry]) -> str:
    needs_xhtml = any(e.alternates for e in entries)
    ns = f"{_URLSET_NS} {_XHTML_NS}" if needs_xhtml else _URLSET_NS
    body = "".join(_url_xml(e) for e in entries)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset {ns}>{body}</urlset>'


def _sitemap_index_xml(child_filenames: list[str], *, lastmod: str) -> str:
    entries = []
    for name in child_filenames:
        loc = absolute(f"/{name}")
        entries.append(
            f"<sitemap><loc>{escape(loc)}</loc><lastmod>{lastmod}</lastmod></sitemap>"
        )
    body = "".join(entries)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f"<sitemapindex {_INDEX_NS}>{body}</sitemapindex>"
    )


def _static_entries(items: list[ArticleFeedItem]) -> list[_UrlEntry]:
    newest = max((i.published_at_epoch for i in items), default=int(time.time()))
    lastmod = _iso_date(newest)
    return [
        _UrlEntry(loc=site_url() + "/", lastmod=lastmod, changefreq="hourly"),
        _UrlEntry(loc=absolute("/about"), changefreq="monthly"),
        _UrlEntry(loc=absolute("/contact"), changefreq="monthly"),
        *[
            _UrlEntry(loc=absolute(f"/section/{s.slug}"), changefreq="daily")
            for s in SECTIONS
        ],
    ]


def _article_entries(
    items: list[ArticleFeedItem],
    translations_by_id: dict[str, list[str]],
) -> list[_UrlEntry]:
    """One <url> per locale variant, each carrying the full hreflang cluster."""
    entries: list[_UrlEntry] = []
    for item in items:
        alternates = article_hreflang_links(
            item.article_id, translations_by_id.get(item.article_id)
        )
        lastmod = _iso_date(item.published_at_epoch)
        seen_locs: set[str] = set()
        for _hreflang, loc in alternates:
            if loc in seen_locs:
                continue
            seen_locs.add(loc)
            entries.append(_UrlEntry(loc=loc, lastmod=lastmod, alternates=alternates))
    return entries


def build_sitemaps(
    items: list[ArticleFeedItem],
    translations_by_id: dict[str, list[str]],
) -> SitemapBuild:
    static = _static_entries(items)
    articles = _article_entries(items, translations_by_id)
    all_entries = static + articles
    newest = _iso_date(max((i.published_at_epoch for i in items), default=int(time.time())))

    if len(all_entries) <= MAX_URLS_PER_SITEMAP:
        return SitemapBuild(is_index=False, root_xml=_urlset_xml(all_entries), parts={})

    parts: dict[str, str] = {"sitemap-pages.xml": _urlset_xml(static)}
    for i, chunk in enumerate(_chunk(articles, MAX_URLS_PER_SITEMAP), 1):
        parts[f"sitemap-articles-{i}.xml"] = _urlset_xml(chunk)

    return SitemapBuild(
        is_index=True,
        root_xml=_sitemap_index_xml(list(parts.keys()), lastmod=newest),
        parts=parts,
    )


def sitemap_xml(
    items: list[ArticleFeedItem],
    translations_by_id: dict[str, list[str]] | None = None,
) -> str:
    """Build the root sitemap (single urlset or index when split)."""
    return build_sitemaps(items, translations_by_id or {}).root_xml


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
