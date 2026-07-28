"""robots.txt and XML sitemaps built from the live news feed."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from app.core.config import settings
from app.modules.news.models.schemas import ArticleFeedItem
from app.modules.seo.render import absolute, article_hreflang_links, article_path, site_url
from app.modules.seo.topics import reliable_tags

logger = logging.getLogger(__name__)

# Google News only wants articles from roughly the last two days.
_NEWS_WINDOW_SECONDS = 48 * 3600
# Google's hard cap is 50k URLs / 50MB per file; split well before that.
MAX_URLS_PER_SITEMAP = 5000

_URLSET_NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
_XHTML_NS = 'xmlns:xhtml="http://www.w3.org/1999/xhtml"'
_INDEX_NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'

_tombstone_cache: dict[str, object] = {"mono": 0.0, "ids": set()}


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
    """Render robots.txt, disallowing admin/search/suggestions and pointing at the sitemap(s)."""
    lines = [
        "User-agent: *",
        "Allow: /",
        # /search and /suggestions are deliberately NOT disallowed: they send
        # `noindex, follow`, and a crawler blocked by robots.txt never reads the
        # noindex, so the URLs could still surface bare. /admin stays blocked —
        # it is gated anyway and there is nothing there to crawl.
        "Disallow: /admin",
        "",
        f"Sitemap: {site_url()}/sitemap.xml",
    ]
    if settings.seo_news_sitemap_enabled:
        lines.append(f"Sitemap: {site_url()}/sitemap-news.xml")
    return "\n".join(lines) + "\n"


def llms_txt() -> str:
    """llms.txt (llmstxt.org): a markdown site guide for AI crawlers — a real audience here (their share of bot traffic is tracked as a first-class analytics stat). Points them at the full-content feed and sitemap instead of leaving them to scrape the Flutter shell."""
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
        f"- [Sitemap]({absolute('/sitemap.xml')}): all article and topic URLs "
        "(multilingual; auto-split when large)",
        f"- [Topics]({absolute('/topics')}): every topic the newsroom covers, "
        "with per-topic article listings under /topic/<tag>",
        f"- [Per-topic RSS]({absolute('/feed/topic/sdk.xml')}): replace `sdk` with "
        "any topic slug — one feed per writer tag",
        f"- [About]({absolute('/about')}): editorial and AI-authorship disclosure",
        f"- [Contact]({absolute('/contact')}): corrections, tips and feedback form",
    ]
    return "\n".join(lines) + "\n"


def _iso_date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).date().isoformat()


def _chunk(entries: list[_UrlEntry], size: int) -> list[list[_UrlEntry]]:
    return [entries[i : i + size] for i in range(0, len(entries), size)]


def _hreflang_link(hreflang: str, href: str) -> str:
    return f'<xhtml:link rel="alternate" hreflang="{escape(hreflang)}" href="{escape(href)}"/>'


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
        entries.append(f"<sitemap><loc>{escape(loc)}</loc><lastmod>{lastmod}</lastmod></sitemap>")
    body = "".join(entries)
    return f'<?xml version="1.0" encoding="UTF-8"?><sitemapindex {_INDEX_NS}>{body}</sitemapindex>'


def bust_tombstone_cache() -> None:
    """Call after admin delete so the next sitemap build drops the URL immediately."""
    _tombstone_cache["mono"] = 0.0


def _tombstoned_ids(_article_ids: Iterable[str] | None = None) -> set[str]:
    """Article IDs hard-deleted by admin (410 Gone). Full-table scan of a tiny tombstone set, cached briefly so sitemap builds don't hammer Cassandra."""
    now = time.monotonic()
    cached = _tombstone_cache.get("ids")
    cached_at = float(_tombstone_cache.get("mono", 0.0))
    if isinstance(cached, set) and now - cached_at < 60:
        return cached
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import DeletedArticleStmts

        rows = get_cassandra_session().execute(DeletedArticleStmts.LIST_IDS)
        picked = {str(row.article_id) for row in rows}
    except Exception:
        logger.debug("tombstone lookup failed; sitemap omits no extra filter", exc_info=True)
        picked = set()
    _tombstone_cache["mono"] = now
    _tombstone_cache["ids"] = picked
    return picked


def _static_entries(items: list[ArticleFeedItem]) -> list[_UrlEntry]:
    newest = max((i.published_at_epoch for i in items), default=int(time.time()))
    lastmod = _iso_date(newest)
    topic_entries = []
    for tag, _count in reliable_tags(items):
        wanted = tag.lower()
        topic_newest = max(
            (
                i.published_at_epoch
                for i in items
                if any(t.strip().lower() == wanted for t in (i.tags or []))
            ),
            default=None,
        )
        topic_entries.append(
            _UrlEntry(
                loc=absolute(f"/topic/{tag}"),
                lastmod=_iso_date(topic_newest) if topic_newest else None,
                changefreq="daily",
            )
        )
    return [
        _UrlEntry(loc=site_url() + "/", lastmod=lastmod, changefreq="hourly"),
        _UrlEntry(loc=absolute("/news"), lastmod=lastmod, changefreq="hourly"),
        _UrlEntry(loc=absolute("/hot"), lastmod=lastmod, changefreq="hourly"),
        _UrlEntry(loc=absolute("/about"), changefreq="monthly"),
        _UrlEntry(loc=absolute("/contact"), changefreq="monthly"),
        _UrlEntry(loc=absolute("/topics"), lastmod=lastmod, changefreq="daily"),
        *topic_entries,
    ]


def _article_entries(
    items: list[ArticleFeedItem],
    translations_by_id: dict[str, list[str]],
) -> list[_UrlEntry]:
    """One <url> per locale variant, each carrying the full hreflang cluster."""
    tombstones = _tombstoned_ids()
    entries: list[_UrlEntry] = []
    for item in items:
        if item.article_id in tombstones:
            continue
        # Pass the slug: without it the sitemap advertises uuid URLs while
        # rel=canonical advertises slugs, which is the mixed state that reads
        # as duplicate content.
        alternates = article_hreflang_links(
            item.article_id, translations_by_id.get(item.article_id), item.slug
        )
        # Revised articles advertise the revision date so crawlers recrawl.
        lastmod = _iso_date(
            max(item.published_at_epoch, getattr(item, "updated_at_epoch", None) or 0)
        )
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
    """Build the full urlset, splitting into a sitemap index plus chunked files once it exceeds the URL cap."""
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
    """Build the Google News sitemap, limited to non-tombstoned articles from the last 48 hours."""
    cutoff = int(time.time()) - _NEWS_WINDOW_SECONDS
    tombstones = _tombstoned_ids()
    recent = [i for i in items if i.published_at_epoch >= cutoff and i.article_id not in tombstones]
    entries = []
    for item in recent:
        pub = datetime.fromtimestamp(item.published_at_epoch, tz=UTC).isoformat()
        keywords = ""
        if item.tags:
            keywords = f"<news:keywords>{escape(', '.join(item.tags))}</news:keywords>"
        entries.append(
            "<url>"
            f"<loc>{escape(absolute(article_path(item.article_id, item.slug)))}</loc>"
            "<news:news>"
            "<news:publication>"
            f"<news:name>{escape(settings.site_name)}</news:name>"
            "<news:language>en</news:language>"
            "</news:publication>"
            f"<news:publication_date>{pub}</news:publication_date>"
            f"<news:title>{escape(item.title)}</news:title>"
            f"{keywords}"
            "</news:news>"
            "</url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
        f"{''.join(entries)}</urlset>"
    )
