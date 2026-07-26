"""Markdown-to-HTML/text rendering used by the SSR document routes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.seo import feeds, render, shell, sitemap
from app.modules.seo.api.routes import _is_known_app_path
from app.modules.seo.markdown import md_to_html, md_to_text, truncate
from app.modules.seo.topics import SECTION_REDIRECTS, reliable_tags


def _article(**kw: object) -> ArticleDetail:
    base = {
        "article_id": "abc123",
        "service_id": "svc",
        "title": "Algorand Foundation Launches New Tool",
        "summary": "A concise summary of the announcement.",
        "body": "## Heading\n\nBody **text** with a [link](https://x.io).\n\n- one\n- two",
        "published_at_epoch": 1_750_000_000,
        "tags": ["sdk", "release"],
        "image_url": "https://img.io/hero.png",
        "source_url": "https://src.io/a",
    }
    base.update(kw)
    return ArticleDetail(**base)


def _feed(n: int, *, epoch: int = 1_750_000_000) -> list[ArticleFeedItem]:
    return [
        ArticleFeedItem(
            article_id=f"id{i}",
            service_id="svc",
            title=f"Title {i}",
            summary=f"Summary {i}",
            published_at_epoch=epoch + i,
            tags=["sdk"] if i % 2 else ["market"],
        )
        for i in range(n)
    ]


# --- markdown ----------------------------------------------------------------


def test_md_to_html_blocks_and_inline() -> None:
    """Renders headings, bold, italic, inline code and lists to HTML."""
    html = md_to_html("# Title\n\nHello **bold** and *em* and `code`.\n\n- a\n- b")
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>em</em>" in html
    assert "<code>code</code>" in html
    assert "<ul>" in html
    assert "<li>a</li>" in html


def test_md_link_is_escaped_and_nofollow() -> None:
    """Renders links with escaped href and a noopener nofollow rel."""
    html = md_to_html("See [click](https://e.x/p?q=1).")
    assert 'href="https://e.x/p?q=1"' in html
    assert 'rel="noopener nofollow"' in html


def test_md_escapes_html() -> None:
    """Escapes raw HTML embedded in markdown source instead of passing it through."""
    html = md_to_html("a <script>alert(1)</script> b")
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_md_to_text_strips_markup() -> None:
    """Strips markdown syntax down to plain text while keeping the content."""
    text = md_to_text("# H\n\n**bold** [x](https://a.b) `c`")
    assert "#" not in text
    assert "*" not in text
    assert "`" not in text
    assert "bold" in text
    assert "x" in text


def test_truncate_word_boundary() -> None:
    """Truncates text at a word boundary and appends an ellipsis."""
    out = truncate("one two three four five", 12)
    assert len(out) <= 13
    assert out.endswith("…")


# --- article rendering -------------------------------------------------------


def test_render_article_head_has_core_tags() -> None:
    """Renders an article's head/body with canonical, OG, Twitter and JSON-LD tags plus site chrome."""
    head, body = render.render_article(_article())
    assert head.count("<title>") == 1
    assert 'rel="canonical" href="https://algorand.pxke.me/news/articles/abc123"' in head
    assert 'property="og:type" content="article"' in head
    assert "img.io/hero.png" in head
    assert 'name="twitter:card" content="summary_large_image"' in head
    assert 'id="ssr-body"' in body
    assert "<h1>" in body
    # Site chrome: masthead, primary nav and footer links on every page.
    assert 'class="ssr-brand"' in body
    assert 'href="/"' in body
    assert 'href="/news"' in body
    assert 'href="/topics"' in body
    assert 'class="ssr-footer"' in body
    assert 'href="/contact"' in body
    assert 'href="/feed.xml"' in body
    assert 'href="/sitemap.xml"' in body
    assert 'aria-label="Breadcrumb"' in body
    # Tag links, back link and syndication footer.
    assert 'href="/topic/sdk"' in body
    assert 'rel="tag"' in body
    assert "← Latest stories" in body
    # Visible SSR content, NOT noscript (Google renders JS and ignores noscript,
    # and the canvas Flutter app has no DOM text) + self-removal on app paint.
    assert "<noscript>" not in body
    assert "pxke-spa-ready" in body


def test_ssr_script_restores_server_title_after_spa_mount() -> None:
    """Flutter web's MaterialApp(title:) overwrites document.title with the static app name during its first build, so rendering crawlers (Bing, Google WRS) saw one generic title on every route (2026-07-09 Bing audit). The first-frame script must capture the server-sent title at parse time and restore it after Flutter paints."""
    body = render.ssr_container("<h1>x</h1>")
    assert "var pxkeSsrTitle=document.title" in body
    assert "document.title=pxkeSsrTitle" in body
    # Restore must run AFTER Flutter's own title write (post-first-frame).
    assert body.index("pxke-spa-ready") > body.index("document.title=pxkeSsrTitle")


def test_render_article_hreflang_for_translations() -> None:
    """Renders hreflang alternates and a visible translation picker for a translated article."""
    head, body = render.render_article(
        _article(),
        lang="fa",
        translation_langs=["fa", "ar"],
    )
    assert 'rel="alternate" hreflang="x-default"' in head
    assert 'hreflang="fa-AF"' in head
    assert 'hreflang="ar"' in head
    assert "?lang=fa" in head
    assert 'property="og:locale" content="fa_AF"' in head
    assert 'rel="canonical" href="https://algorand.pxke.me/news/articles/abc123?lang=fa"' in head
    # Visible translation picker in the body (not just <head> hreflang).
    assert 'aria-label="Translations"' in body
    assert 'hreflang="fa-AF"' in body
    assert 'aria-current="true"' in body
    assert "Dari" in body
    assert 'hreflang="ar"' in body
    assert "?lang=ar" in body


def test_render_article_jsonld_is_valid_newsarticle() -> None:
    """Renders a valid schema.org NewsArticle JSON-LD block."""
    head, _ = render.render_article(_article())
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', head, re.DOTALL)
    assert m
    data = json.loads(m.group(1).replace("<\\/", "</"))
    assert data["@type"] == "NewsArticle"
    assert data["headline"]
    assert data["datePublished"].startswith("20")
    assert data["articleBody"]
    assert data["wordCount"] > 0
    assert data["articleSection"] == "sdk"
    assert data["speakable"]["cssSelector"]
    assert data["publisher"]["name"] == "PXke Algorand"


def test_render_article_og_section_from_primary_tag() -> None:
    """Sets article:section from the article's primary tag."""
    head, _ = render.render_article(_article(tags=["sdk", "release"]))
    assert 'property="article:section" content="sdk"' in head


def test_render_article_falls_back_to_generated_share_card() -> None:
    """No real photo -> a per-article generated OG card (proper 1200x630 aspect, names the story) instead of the generic square app icon. The Schema.org publisher `logo` legitimately still points at the site icon — that's the organization's brand mark, a different field from og:image — so this checks the image METAS specifically, not every occurrence."""
    head, _ = render.render_article(_article(image_url=None))
    assert 'property="og:image" content="https://algorand.pxke.me/og/article/abc123.png"' in head
    assert 'name="twitter:image" content="https://algorand.pxke.me/og/article/abc123.png"' in head


def test_render_noindex_marks_robots() -> None:
    """Renders a noindex page with the robots meta and standard nav chrome."""
    head, body = render.render_noindex("Admin")
    assert 'name="robots" content="noindex, follow"' in head
    assert 'class="ssr-nav"' in body
    assert 'href="/about"' in body


def test_ssr_chrome_lists_popular_topics_in_footer() -> None:
    """Lists reliable topics in the front-page SSR footer."""
    items = _feed(4)
    topics = reliable_tags(items)
    _, body = render.render_front(_feed(4), [], topic_links=topics)
    assert 'id="ssr-topics-h"' in body
    assert 'href="/topic/market"' in body or 'href="/topic/sdk"' in body


def test_article_has_breadcrumb_and_image_meta() -> None:
    """Renders a BreadcrumbList and generated share-card image metas when no hero image is set."""
    head, _ = render.render_article(_article(image_url=None, tags=["sdk"]))
    assert '"@type":"BreadcrumbList"' in head
    assert '"name":"sdk"' in head  # primary-tag crumb -> /topic/sdk
    # generated share card -> known (og-standard) dimensions + alt on og/twitter
    assert 'property="og:image:width" content="1200"' in head
    assert 'property="og:image:height" content="630"' in head
    assert 'property="og:image:alt"' in head
    assert 'name="twitter:image:alt"' in head


def test_article_icon_like_image_also_gets_share_card() -> None:
    """A source favicon/logo in image_url (the workers' brand-icon fallback) must not leak into og:image as a stretched square icon — it gets the same generated card as a fully missing image."""
    head, _ = render.render_article(_article(image_url="https://x.io/favicon.ico"))
    assert "/og/article/abc123.png" in head
    assert "favicon.ico" not in head


def test_article_real_image_omits_dimensions() -> None:
    """Omits og:image dimensions for a real hero image while keeping alt text."""
    head, _ = render.render_article(_article(image_url="https://img.io/h.png"))
    assert "og:image:width" not in head  # unknown dims for a real hero image
    assert 'property="og:image:alt"' in head  # alt still present


def test_head_has_rss_alternate_link() -> None:
    """Includes an RSS autodiscovery link in the news feed head."""
    head, _ = render.render_news_feed(_feed(2))
    assert 'type="application/rss+xml"' in head
    assert "/feed.xml" in head


def test_home_has_website_searchaction_and_org(monkeypatch: pytest.MonkeyPatch) -> None:
    """Includes WebSite/SearchAction JSON-LD and sameAs links on the home page."""
    monkeypatch.setattr(render.settings, "seo_same_as", "https://x.com/pxke")
    head, _ = render.render_front(_feed(2), [])
    assert '"@type":"WebSite"' in head
    assert '"@type":"SearchAction"' in head
    assert "/search?q={search_term_string}" in head
    assert '"sameAs":["https://x.com/pxke"]' in head


# --- RSS feed ----------------------------------------------------------------


def test_rss_feed_structure() -> None:
    """Builds a well-formed RSS 2.0 feed with one item per article."""
    xml = feeds.rss_xml(_feed(3))
    assert xml.startswith("<?xml")
    assert '<rss version="2.0"' in xml
    assert "<channel>" in xml
    assert xml.count("<item>") == 3
    assert "https://algorand.pxke.me/news/articles/id0" in xml
    assert "<pubDate>" in xml
    assert 'rel="self"' in xml


# --- home / section ----------------------------------------------------------


def test_render_home_lists_articles() -> None:
    """Lists all feed articles with a CollectionPage head and matching body links."""
    head, body = render.render_news_feed(_feed(3))
    assert "CollectionPage" in head
    assert 'rel="canonical" href="https://algorand.pxke.me/news"' in head
    assert 'id="pxke-ssr-feed"' in head
    assert '"items":' in head
    assert body.count('href="/news/articles/id') == 3


def test_render_front_has_editorial_sections() -> None:
    """Renders the front page with lead, top-stories and most-read sections."""
    items = _feed(8)
    hot = _feed(3)
    head, body = render.render_front(items, hot)
    assert 'rel="canonical" href="https://algorand.pxke.me/"' in head
    assert 'class="ssr-front"' in body
    assert 'class="ssr-lead"' in body
    assert "Top stories" in body
    assert 'href="/hot">Most read' in body
    assert "More news" in body
    assert "Full chronological feed" in body
    assert 'id="pxke-ssr-feed"' in head


def test_render_front_differs_from_news_feed() -> None:
    """Distinguishes the front page's editorial layout from the plain chronological feed."""
    items = _feed(6)
    _, front = render.render_front(items, _feed(2))
    _, news = render.render_news_feed(items)
    assert 'class="ssr-front"' in front
    assert 'class="ssr-front"' not in news
    assert "Full chronological feed" in front


def test_render_news_canonical_is_distinct_from_home() -> None:
    """Gives the news feed a canonical URL distinct from the home page's."""
    items = _feed(2)
    front_head, _ = render.render_front(items, [])
    news_head, news_body = render.render_news_feed(items)
    assert 'rel="canonical" href="https://algorand.pxke.me/"' in front_head
    assert 'rel="canonical" href="https://algorand.pxke.me/news"' in news_head
    assert "Latest" in news_body
    assert 'aria-label="Breadcrumb"' in news_body


def test_pick_related_articles_shares_tags() -> None:
    """Picks related articles that share at least one tag with the source article."""
    article = _article(tags=["sdk", "release"])
    feed = _feed(5)
    feed[1].tags = ["sdk", "market"]
    feed[3].tags = ["unrelated"]
    related = render.pick_related_articles(article, feed, limit=3)
    assert len(related) == 1
    assert related[0].article_id == "id1"


def test_render_article_related_stories() -> None:
    """Renders a related-stories section linking to each related article."""
    article = _article()
    related = [
        ArticleFeedItem(
            article_id="id9",
            service_id="svc",
            title="Related piece",
            summary="R",
            published_at_epoch=1_750_000_100,
            tags=["sdk"],
        )
    ]
    _, body = render.render_article(article, related=related)
    assert 'class="ssr-related"' in body
    assert "Related piece" in body
    assert 'href="/news/articles/id9"' in body


def test_render_hot_embeds_ssr_feed_json() -> None:
    """Embeds the SSR feed JSON payload in the most-read page head."""
    head, _ = render.render_hot(_feed(3))
    assert 'id="pxke-ssr-feed"' in head


def test_render_topic_truncation_note() -> None:
    """Shows a truncation note and topic RSS link when a topic has more stories than shown."""
    items = _feed(3)
    head, body = render.render_topic("sdk", items, total_count=47)
    assert "Showing 3 of 47 stories" in body
    assert 'id="pxke-ssr-feed"' in head
    assert 'href="https://algorand.pxke.me/feed/topic/sdk.xml"' in head
    assert "Subscribe to this topic (RSS)" in body


def test_topic_rss_feed_xml() -> None:
    """Builds a per-topic RSS feed scoped to that topic's articles."""
    xml = feeds.topic_rss_xml("sdk", _feed(2))
    assert "sdk" in xml
    assert "/feed/topic/sdk.xml" in xml
    assert "/topic/sdk" in xml
    assert xml.count("<item>") == 2


def test_render_topics_lists_per_topic_rss() -> None:
    """Lists a per-topic RSS link on the topics index page."""
    items = _feed(4)
    topics = reliable_tags(items)
    _, body = render.render_topics(topics)
    assert 'href="/feed/topic/market.xml"' in body or 'href="/feed/topic/sdk.xml"' in body


def test_cached_feed_snapshot_reuses_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ARG001 -- name must match the real callee's keyword arg
    """Reuses the cached feed snapshot on a second call within the TTL instead of refetching."""
    from app.modules.seo import topics as topics_mod

    calls = {"n": 0}

    def list_feed(*, limit: int = 500) -> list[ArticleFeedItem]:  # noqa: ARG001 -- name must match the real callee's keyword arg
        calls["n"] += 1
        return _feed(2)

    topics_mod._feed_cache["mono"] = 0.0
    topics_mod.cached_feed_snapshot(list_feed)
    topics_mod.cached_feed_snapshot(list_feed)
    assert calls["n"] == 1


def test_beacon_path_validation_rejects_made_up_paths() -> None:
    # Known static routes and topic slugs pass.
    """Accepts known static/topic/article routes and rejects admin, retired and malformed paths."""
    assert _is_known_app_path("/")
    assert _is_known_app_path("/news")
    assert _is_known_app_path("/hot")
    assert _is_known_app_path("/topics")
    assert _is_known_app_path("/about")
    assert _is_known_app_path("/topic/sdk")
    assert _is_known_app_path("/news/articles/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d")
    # Admin isn't a public pageview route; retired sections, an overlong or
    # nested topic slug, a non-UUID "article id" and arbitrary paths must not
    # bump analytics counters.
    assert not _is_known_app_path("/admin")
    assert not _is_known_app_path("/section/markets")
    assert not _is_known_app_path("/topic/")
    assert not _is_known_app_path("/topic/" + "x" * 49)
    assert not _is_known_app_path("/topic/a/b")
    assert not _is_known_app_path("/news/articles/not-a-uuid")
    assert not _is_known_app_path("/random/garbage")


def test_ssr_track_snippet_marks_recorded_path() -> None:
    """Embeds the given path in the SSR pageview-tracking snippet."""
    snippet = shell.ssr_track_snippet("/news/articles/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d")
    assert 'sessionStorage.setItem("pxke_ssr_pv"' in snippet
    assert "/news/articles/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d" in snippet


def test_reliable_tags_policy_and_section_redirect_map() -> None:
    # 10 stories: "market" on 5, "sdk" on 5 (both hit the 50% ubiquity
    # ceiling), "niche" on 2 (kept), "one-off" on 1 (singleton, dropped).
    """Applies the ubiquity ceiling and singleton drop to reliable_tags and maps every retired section."""
    items = _feed(10)
    items[0].tags = [*items[0].tags, "niche"]
    items[2].tags = [*items[2].tags, "niche"]
    items[4].tags = [*items[4].tags, "one-off"]
    picked = dict(reliable_tags(items))
    assert picked.get("niche") == 2
    assert "one-off" not in picked
    assert "market" not in picked
    assert "sdk" not in picked
    # Every retired section slug redirects to a topic.
    assert set(SECTION_REDIRECTS) == {"markets", "security", "developers", "community", "ecosystem"}


# --- sitemap / robots --------------------------------------------------------


def test_robots_txt_points_to_sitemaps() -> None:
    """Points robots.txt at the main sitemap and disallows the admin path."""
    txt = sitemap.robots_txt()
    assert "Disallow: /admin" in txt
    assert "Sitemap: https://algorand.pxke.me/sitemap.xml" in txt


def test_robots_news_sitemap_gated_by_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Includes or omits the news sitemap link in robots.txt based on the feature flag."""
    monkeypatch.setattr(sitemap.settings, "seo_news_sitemap_enabled", False)
    assert "sitemap-news.xml" not in sitemap.robots_txt()
    monkeypatch.setattr(sitemap.settings, "seo_news_sitemap_enabled", True)
    assert "sitemap-news.xml" in sitemap.robots_txt()


def test_sitemap_xml_includes_articles_and_topics() -> None:
    # 4 stories -> "market"/"sdk" each on 2 (ubiquity waived on tiny corpora).
    """Includes article, topic and static page URLs in the sitemap, excluding retired sections."""
    xml = sitemap.sitemap_xml(_feed(4))
    assert xml.startswith("<?xml")
    assert "https://algorand.pxke.me/news/articles/id0" in xml
    assert "https://algorand.pxke.me/topics" in xml
    assert "https://algorand.pxke.me/news" in xml
    assert "https://algorand.pxke.me/hot" in xml
    assert "https://algorand.pxke.me/topic/market" in xml
    assert "/section/" not in xml


def test_sitemap_article_hreflang_and_translations() -> None:
    """Includes hreflang alternates for an article's translations in the sitemap."""
    items = _feed(1)
    translations = {"id0": ["fa", "ar"]}
    xml = sitemap.sitemap_xml(items, translations)
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in xml
    assert 'hreflang="fa-AF"' in xml
    assert 'hreflang="ar"' in xml
    assert "?lang=fa" in xml
    assert "?lang=ar" in xml
    assert 'hreflang="x-default"' in xml


def test_sitemap_splits_into_index_when_large(monkeypatch: pytest.MonkeyPatch) -> None:
    """Splits the sitemap into an index plus chunked page/article files once the URL cap is exceeded."""
    monkeypatch.setattr(sitemap, "MAX_URLS_PER_SITEMAP", 4)
    items = _feed(3)
    translations = {f"id{i}": ["fa"] for i in range(3)}
    build = sitemap.build_sitemaps(items, translations)
    assert build.is_index
    assert "<sitemapindex" in build.root_xml
    assert "sitemap-pages.xml" in build.root_xml
    assert "sitemap-articles-1.xml" in build.root_xml
    assert "sitemap-pages.xml" in build.parts
    assert "sitemap-articles-1.xml" in build.parts
    pages = build.parts["sitemap-pages.xml"]
    assert "/topics" in pages
    assert "?lang=fa" not in pages
    articles = build.parts["sitemap-articles-1.xml"]
    assert "?lang=fa" in articles
    assert 'hreflang="fa-AF"' in articles


def test_sitemap_single_file_when_under_limit() -> None:
    """Builds a single urlset with no index when under the URL cap."""
    build = sitemap.build_sitemaps(_feed(2), {})
    assert not build.is_index
    assert "<urlset" in build.root_xml
    assert "<sitemapindex" not in build.root_xml
    assert build.parts == {}


def test_news_sitemap_windows_recent_only() -> None:
    """Includes only recently-published articles in the Google News sitemap."""
    import time

    fresh = _feed(2, epoch=int(time.time()) - 3600)
    xml = sitemap.news_sitemap_xml(fresh)
    assert "news:news" in xml
    assert "news:publication_date" in xml
    assert "news:keywords" in xml
    old = sitemap.news_sitemap_xml(_feed(2, epoch=1_700_000_000))
    assert "<url>" not in old


def test_sitemap_excludes_tombstoned_articles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Excludes hard-deleted (tombstoned) articles from the sitemap."""
    items = _feed(3)
    translations: dict[str, list[str]] = {}
    monkeypatch.setattr(sitemap, "_tombstoned_ids", lambda *_: {"id1"})
    xml = sitemap.sitemap_xml(items, translations)
    assert "id0" in xml
    assert "id2" in xml
    assert "id1" not in xml


def test_news_sitemap_excludes_tombstones(monkeypatch: pytest.MonkeyPatch) -> None:
    """Excludes hard-deleted (tombstoned) articles from the news sitemap."""
    import time

    monkeypatch.setattr(sitemap, "_tombstoned_ids", lambda *_: {"id0"})
    fresh = _feed(2, epoch=int(time.time()) - 3600)
    xml = sitemap.news_sitemap_xml(fresh)
    assert "id1" in xml
    assert "id0" not in xml


def test_render_news_feed_truncation_note() -> None:
    """Shows a truncation note on the news feed when more articles exist than are shown."""
    _, body = render.render_news_feed(_feed(3), total_count=120)
    assert "Showing the 3 newest of 120" in body
    assert 'href="/feed.xml"' in body


def test_render_topics_has_collection_jsonld() -> None:
    """Includes CollectionPage/ItemList JSON-LD on the topics index page."""
    tags = [("sdk", 5), ("market", 3)]
    head, _ = render.render_topics(tags)
    assert '"@type":"CollectionPage"' in head
    assert '"@type":"ItemList"' in head


# --- shell injection ---------------------------------------------------------

_SHELL = (
    '<!DOCTYPE html><html lang="en"><head>'
    '<meta name="description" content="old">'
    '<meta property="og:title" content="old">'
    '<meta name="twitter:title" content="old">'
    '<link rel="canonical" href="https://algorand.pxke.me/">'
    '<link rel="alternate" type="application/rss+xml" href="https://algorand.pxke.me/feed.xml">'
    "<title>Old Title</title>"
    '</head><body>\n<script src="flutter_bootstrap.js" async></script></body></html>'
)


def test_shell_injection_dedups_and_keeps_bootstrap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Injects SSR head/body into the Flutter shell once, deduping stale metas and keeping the bootstrap script."""
    (tmp_path / "index.html").write_text(_SHELL, encoding="utf-8")
    monkeypatch.setattr(shell.settings, "frontend_dist_dir", str(tmp_path))
    shell._cache["html"] = None  # bust the module cache

    head, body = render.render_article(_article(), lang="fa", translation_langs=["fa"])
    doc = shell.render_document(head, body, html_lang="fa-AF")
    assert doc is not None
    assert '<html lang="fa-AF">' in doc
    assert doc.count("<title>") == 1
    assert "Old Title" not in doc
    assert doc.count('property="og:title"') == 1
    assert doc.count('name="twitter:title"') == 1
    assert doc.count('rel="canonical"') == 1
    # Scoped to the <link rel="alternate"> autodiscovery tag specifically — the
    # SSR footer also has an intentional, unrelated <a ... type="application/
    # rss+xml"> syndication link that legitimately shares the bare substring.
    assert doc.count('<link rel="alternate" type="application/rss+xml"') == 1
    assert "flutter_bootstrap.js" in doc
    assert 'id="ssr-body"' in doc


def test_candidate_dirs_survives_deleted_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rolling deploy can delete the process WorkingDirectory; Path.cwd() then raises FileNotFoundError — must not take down every SSR route."""
    monkeypatch.setattr(shell.settings, "frontend_dist_dir", None)
    monkeypatch.setattr(shell, "_safe_cwd_roots", lambda: [])
    dirs = shell._candidate_dirs()
    assert dirs  # __file__-relative roots still present
    assert all("frontend_web" in str(d) or "frontend/dist" in str(d) for d in dirs)


def test_shell_injection_preserves_jsonld_escapes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    r"""The head must be injected verbatim: re.sub replacement-string escape processing turned the \\n sequences inside the JSON-LD articleBody into raw newlines — invalid JSON that made Google drop the NewsArticle block."""
    (tmp_path / "index.html").write_text(_SHELL, encoding="utf-8")
    monkeypatch.setattr(shell.settings, "frontend_dist_dir", str(tmp_path))
    shell._cache["html"] = None  # bust the module cache

    head, body = render.render_article(_article(body="line one\n\nline two\n\nline three"))
    doc = shell.render_document(head, body)
    assert doc is not None
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', doc, re.DOTALL):
        data = json.loads(m.group(1).replace("<\\/", "</"))
        assert data["@type"]


def test_indexable_pages_allow_large_image_previews() -> None:
    """Allows large image previews on indexable pages while leaving noindex pages' robots directive untouched."""
    head, _ = render.render_article(_article())
    assert 'name="robots" content="max-image-preview:large"' in head
    # noindex pages keep their explicit directive untouched
    head, _ = render.render_noindex("Admin")
    assert 'content="noindex, follow"' in head
    assert "max-image-preview" not in head


# --- markdown tables / fences --------------------------------------------------


def test_md_table_renders_as_html_table() -> None:
    """Renders a markdown table as an HTML table with no leftover pipe characters."""
    md = "| Wallet | Type |\n|--------|------|\n| Pera | Mobile |\n| Defly | Mobile |"
    html = md_to_html(md)
    assert "<table>" in html
    assert "</table>" in html
    assert "<th>Wallet</th>" in html
    assert "<td>Pera</td>" in html
    assert "|" not in re.sub(r"<[^>]+>", "", html)  # no pipe soup in the text


def test_md_chart_fence_becomes_caption_not_json() -> None:
    """Renders a ```chart fence as a plain caption instead of leaking its JSON payload."""
    md = (
        "Before.\n\n```chart\n"
        '{"type": "bar", "title": "TVL by protocol", "x": ["a"]}\n'
        "```\n\nAfter."
    )
    html = md_to_html(md)
    assert '"type"' not in html
    assert "```" not in html
    assert "Chart: TVL by protocol" in html
    text = md_to_text(md)
    assert '"type"' not in text
    assert "TVL by protocol" not in text
    assert "Before." in text
    assert "After." in text


def test_md_generic_fence_renders_as_code_block() -> None:
    """Renders a generic fenced code block as <pre><code>."""
    html = md_to_html("```python\nprint('hi')\n```")
    assert "<pre><code>" in html
    assert "print(" in html


def test_md_to_text_flattens_tables() -> None:
    """Flattens a markdown table to plain text with no pipes or separator rows."""
    text = md_to_text("| A | B |\n|---|---|\n| 1 | 2 |")
    assert "|" not in text
    assert "---" not in text
    assert "A" in text
    assert "1" in text


# --- full-content RSS / llms.txt ------------------------------------------------


def test_rss_full_content_encoded() -> None:
    """Encodes full article bodies into escaped content:encoded elements in the RSS feed."""
    items = _feed(2)
    xml = feeds.rss_xml(items, bodies={"id0": "<p>Full body zero</p>"})
    assert 'xmlns:content="http://purl.org/rss/1.0/modules/content/"' in xml
    assert "&lt;p&gt;Full body zero&lt;/p&gt;" in xml
    assert xml.count("content:encoded") == 2  # open+close for the one item with a body


def test_llms_txt_lists_feed_and_topics() -> None:
    """Lists the feed, sitemap and topics links in llms.txt, excluding retired sections."""
    txt = sitemap.llms_txt()
    assert txt.startswith("# PXke Algorand")
    assert "feed.xml" in txt
    assert "sitemap.xml" in txt
    assert "/topics" in txt
    assert "/feed/topic/" in txt
    assert "/section/" not in txt


    # API preconnect removed: feed/markets/auth are deferred; early preconnect
    # triggered Lighthouse "unused preconnect" and competed with WASM on boot.


# --- title length budget + SSR visibility -------------------------------------


def test_short_title_keeps_brand_suffix() -> None:
    """Keeps the brand suffix on a short title."""
    head, _ = render.render_article(_article(title="Short headline"))
    assert "<title>Short headline — " in head


def test_long_title_drops_brand_suffix_but_stays_whole() -> None:
    """Drops the brand suffix but keeps the full title intact once it's already near the length budget."""
    t = "Algorand Foundation Restructures Leadership For The Coming Years"  # 65 chars
    head, _ = render.render_article(_article(title=t))
    assert f"<title>{t}</title>" in head


def test_overlong_title_clamped_at_word_boundary() -> None:
    """Clamps an overlong title at a word boundary while leaving the full title in og:title."""
    t = (
        "Algorand Foundation Restructures Leadership to Accelerate "
        "AI-Driven On-Chain Activity Across the Entire Ecosystem"
    )
    head, _ = render.render_article(_article(title=t))
    import re

    m = re.search(r"<title>(.*?)</title>", head)
    assert m is not None
    assert len(m.group(1)) <= 66  # 65 + ellipsis
    assert m.group(1).endswith("…")
    # Full headline still rides in og:title untouched.
    assert f'property="og:title" content="{t}"' in head


def test_spa_ready_script_removes_ssr_from_dom() -> None:
    # SSR is in the initial HTML for crawlers; after Flutter paints the script
    # removes #ssr-body so Ctrl+F does not match text under the canvas.
    """Removes the SSR body from the DOM via the first-frame script after Flutter paints."""
    _, body = render.render_article(_article())
    assert "pxke-spa-ready" in body
    assert 'id="ssr-body"' in body
    assert "ssr-body" in body
    assert ".remove()" in body
    assert "aria-hidden" not in body


# --- icon-like image_url must not become a hero or share image -----------------


def test_favicon_image_url_never_becomes_hero_or_og_image() -> None:
    """Falls back to the generated share card and omits a hero <img> when image_url is favicon-like."""
    head, body = render.render_article(_article(image_url="https://brain-chain.app/favicon.svg"))
    assert "favicon.svg" not in head
    assert "favicon.svg" not in body
    # Metas fall back to the generated share card instead.
    assert 'property="og:image" content="https://algorand.pxke.me/og/article/' in head
    # No stretched-icon hero in the SSR body.
    assert "<img" not in body


def test_icon_named_png_also_treated_as_icon() -> None:
    """Treats a logo-named PNG as icon-like, omitting it as a hero image too."""
    head, body = render.render_article(_article(image_url="https://example.com/logo-dark.png"))
    assert "logo-dark.png" not in head
    assert "<img" not in body


def test_real_share_image_still_renders_hero() -> None:
    """Renders a real (non-icon-like) image as both the share image and the body hero."""
    head, body = render.render_article(_article())
    assert "img.io/hero.png" in head
    assert '<img src="https://img.io/hero.png"' in body


def test_icon_word_boundary_matching() -> None:
    """Matches icon-like filenames on word boundaries, not as bare substrings."""
    from app.modules.seo.render import _is_icon_like

    assert _is_icon_like("https://x.io/algorand_logo_mark_black-Feb.png")
    assert _is_icon_like("https://x.io/valar-solutions-full-logo-preview.png")
    assert _is_icon_like("https://x.io/apple-touch-icon.png")
    assert _is_icon_like("https://x.io/anything.svg")
    assert not _is_icon_like("https://x.io/silicon.png")
    assert not _is_icon_like("https://x.io/features/hero-image.jpg")


def test_chain_only_tag_gets_friendly_display_label() -> None:
    """primary_tag() returns the raw slug (used for /topic/<tag> URLs); the breadcrumb, og:section and articleSection show display_tag_label's friendlier text instead — mirrors the Flutter displayTagLabel mapping."""
    head, _ = render.render_article(_article(tags=["chain-only", "discovery", "payments"]))
    assert '"name":"on-chain"' in head
    assert 'property="article:section" content="on-chain"' in head
    assert '"articleSection":"on-chain"' in head
    assert "/topic/chain-only" in head  # URL slug stays raw
