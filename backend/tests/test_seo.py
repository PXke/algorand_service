"""Markdown-to-HTML/text rendering used by the SSR document routes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.http import QueryParams, Request
from app.modules.ecosystem.models.domain import STATUS_APPROVED, StoredProject
from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.seo import feeds, render, shell, sitemap, topics
from app.modules.seo.api.routes import (
    _doc_response,
    _is_known_app_path,
    article,
    home,
    registry_entry,
    registry_index,
    robots,
    sitemap_root,
)
from app.modules.seo.markdown import md_to_html, md_to_text, truncate
from app.modules.seo.topics import SECTION_REDIRECTS, reliable_tags
from app.modules.x402_directory.models.domain import StoredListing


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


def _feed_with_reliable_topic(
    tag: str, count: int, total: int, *, epoch: int = 1_750_000_000
) -> list[ArticleFeedItem]:
    """`total` items; the first `count` share `tag` (clears MIN_COUNT without tripping the ubiquity ceiling), the rest each carry a unique singleton tag so they never accumulate."""
    return [
        ArticleFeedItem(
            article_id=f"id{i}",
            service_id="svc",
            title=f"Title {i}",
            summary=f"Summary {i}",
            published_at_epoch=epoch + i,
            tags=[tag] if i < count else [f"filler-{i}"],
        )
        for i in range(total)
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


def test_md_relative_glossary_link_becomes_absolute_anchor() -> None:
    """Glossary auto-links (`[ASA](/glossary/asa "definition")`, per glossary_linker.py) become a real, absolute <a href> instead of raw bracket text."""
    html = md_to_html('See the [ASA](/glossary/asa "Algorand Standard Asset, a token type") page.')
    assert 'href="https://algorand.pxke.me/glossary/asa"' in html
    assert ">ASA</a>" in html
    assert "[ASA]" not in html
    assert '"Algorand Standard Asset' not in html  # title text isn't rendered


def test_md_relative_link_without_title() -> None:
    """Relative links without a title (`[text](/path)`) also resolve to an absolute href."""
    html = md_to_html("See [glossary](/glossary/asa) for more.")
    assert 'href="https://algorand.pxke.me/glossary/asa"' in html


def test_md_to_text_strips_relative_glossary_links() -> None:
    """Plain-text rendering (meta descriptions, JSON-LD articleBody) also resolves glossary-style relative links down to their link text."""
    text = md_to_text('An [ASA](/glossary/asa "Algorand Standard Asset") is a token.')
    assert "[ASA]" not in text
    assert "/glossary/asa" not in text
    assert "ASA is a token." in text


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
    assert 'http-equiv="content-language" content="en"' in head
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
    assert 'hreflang="fa"' in head
    assert 'hreflang="ar"' in head
    assert "/fa/news/articles/" in head
    assert 'property="og:locale" content="fa_IR"' in head
    assert 'rel="canonical" href="https://algorand.pxke.me/fa/news/articles/abc123"' in head
    # Bing doesn't read hreflang at all — content-language is the page-level
    # signal it actually uses, and must reflect the REQUESTED locale (fa),
    # not English, on a translated page.
    assert 'http-equiv="content-language" content="fa"' in head
    # Visible translation picker in the body (not just <head> hreflang).
    assert 'aria-label="Translations"' in body
    assert 'hreflang="fa"' in body
    assert 'aria-current="true"' in body
    assert "Persian" in body
    assert 'hreflang="ar"' in body
    assert "/ar/news/articles/" in body


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
    # Display label, not the raw slug: article:section is read by humans.
    # /topic/<tag> URLs still use the slug — see the topic-link asserts.
    assert data["articleSection"] == "SDKs"
    assert data["speakable"]["cssSelector"]
    assert data["publisher"]["name"] == "PXke Algorand"


def test_render_article_og_section_from_primary_tag() -> None:
    """Sets article:section from the article's primary tag."""
    head, _ = render.render_article(_article(tags=["sdk", "release"]))
    assert 'property="article:section" content="SDKs"' in head


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
    items = _feed_with_reliable_topic("market", 10, 25)
    topics = reliable_tags(items)
    _, body = render.render_front(items, [], topic_links=topics)
    assert 'id="ssr-topics-h"' in body
    assert 'href="/topic/market"' in body


def test_article_has_breadcrumb_and_image_meta() -> None:
    """Renders a BreadcrumbList and generated share-card image metas when no hero image is set."""
    head, _ = render.render_article(_article(image_url=None, tags=["sdk"]))
    assert '"@type":"BreadcrumbList"' in head
    assert '"name":"SDKs"' in head  # primary-tag crumb -> /topic/sdk
    # generated share card -> known (og-standard) dimensions + alt on og/twitter
    assert 'property="og:image:width" content="1200"' in head
    assert 'property="og:image:height" content="630"' in head
    assert 'property="og:image:alt"' in head
    assert 'name="twitter:image:alt"' in head


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
    # Canonical is the front page — see test_render_news_canonicalises_to_the_front_page.
    assert 'rel="canonical" href="https://algorand.pxke.me/"' in head
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


def test_render_news_canonicalises_to_the_front_page() -> None:
    """Points /news at / as canonical, because the two are near-identical.

    This asserted a DISTINCT self-canonical until 2026-07-28. In production the
    two pages measured 95% identical text — they list the same stories — so
    Search Console reported "Duplicate without user-selected canonical" and
    picked one itself. Declaring the stronger URL is what we mean; /news stays
    crawlable and linked, it is just not indexed as a second copy. The ItemList
    JSON-LD still describes /news, since it describes THIS page's contents.
    """
    items = _feed(2)
    front_head, _ = render.render_front(items, [])
    news_head, news_body = render.render_news_feed(items)
    assert 'rel="canonical" href="https://algorand.pxke.me/"' in front_head
    assert 'rel="canonical" href="https://algorand.pxke.me/"' in news_head
    assert 'rel="canonical" href="https://algorand.pxke.me/news"' not in news_head
    assert 'name="robots" content="noindex, follow"' in news_head
    assert '"url":"https://algorand.pxke.me/news"' in news_head
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


def test_pick_related_articles_ignores_meta_tag_only_matches() -> None:
    """Sharing only a provenance/meta tag (discovery, web, ...) is not relatedness.

    Regression for a real bug (found 2026-08-09): almost every service-discovery
    article carries "discovery"/"web", so an unfiltered tag-intersection matched
    a DeFi swap-aggregator piece against dozens of unrelated articles that only
    shared those two provenance tags, not an actual topic.
    """
    article = _article(tags=["defi", "discovery", "web"])
    feed = _feed(3)
    feed[0].tags = ["discovery", "web"]  # only provenance overlap -- not related
    feed[1].tags = ["defi"]  # genuine topical overlap -- related
    feed[2].tags = ["nft"]  # no overlap at all
    related = render.pick_related_articles(article, feed, limit=5)
    assert [item.article_id for item in related] == ["id1"]


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
    items = _feed_with_reliable_topic("market", 10, 25)
    topics = reliable_tags(items)
    _, body = render.render_topics(topics)
    assert 'href="/feed/topic/market.xml"' in body


def test_cached_feed_snapshot_reuses_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ARG001 -- name must match the real callee's keyword arg
    """Reuses the cached feed snapshot on a second call within the TTL instead of refetching."""
    from app.modules.seo import topics as topics_mod

    calls = {"n": 0}
    stored: list[dict[str, object]] = []

    def list_feed(*, limit: int = 500) -> list[ArticleFeedItem]:  # noqa: ARG001 -- name must match the real callee's keyword arg
        calls["n"] += 1
        return _feed(2)

    def fake_cached_json(
        _key: str, _ttl: int, compute: Callable[[], dict[str, object]]
    ) -> dict[str, object]:
        if not stored:
            stored.append(compute())
        return stored[0]

    monkeypatch.setattr(topics_mod, "cached_json", fake_cached_json)
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
    # Article paths accept a slug as well as a uuid (migration 056).
    assert _is_known_app_path("/news/articles/algorank-debuts-1k-project-directory")
    # Admin isn't a public pageview route; retired sections, an overlong or
    # nested topic slug, a malformed "article id" and arbitrary paths must not
    # bump analytics counters.
    assert not _is_known_app_path("/admin")
    assert not _is_known_app_path("/section/markets")
    assert not _is_known_app_path("/topic/")
    assert not _is_known_app_path("/topic/" + "x" * 49)
    assert not _is_known_app_path("/topic/a/b")
    assert not _is_known_app_path("/news/articles/Not A Slug")
    assert not _is_known_app_path("/news/articles/nested/path")
    assert not _is_known_app_path("/random/garbage")


def test_ssr_track_snippet_marks_recorded_path() -> None:
    """Embeds the given path in the SSR pageview-tracking snippet."""
    snippet = shell.ssr_track_snippet("/news/articles/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d")
    assert 'sessionStorage.setItem("pxke_ssr_pv"' in snippet
    assert "/news/articles/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d" in snippet


def test_doc_response_dedup_marker_matches_the_browser_path_not_the_counted_one() -> None:
    """The SSR dedup marker must embed dedup_path (what window.location.pathname will actually be), not tracked_path (the canonical path used for counting) -- these differ for a translated article.

    Root-caused 2026-07-30: a direct visit to a locale-prefixed article
    (/fr/news/articles/slug) wrote the CANONICAL path into the dedup marker.
    On boot the SPA compares that marker to window.location.pathname
    (/fr/news/articles/slug) -- they never matched, so the dedup skip never
    fired, and every direct load of a translated article recorded a second,
    redundant pageview under the raw locale path (which analytics could not
    resolve to a title at all -- see test_analytics.py's canonical-path
    tests). tracked_path alone (no dedup_path) must still work for every
    other route, where the two are the same string.
    """
    resp = _doc_response(
        ("<title>t</title>", "<body-html>"),
        "public, max-age=300",
        tracked_path="/news/articles/abc123",
        dedup_path="/fr/news/articles/some-slug",
    )
    doc = resp.description
    assert 'pxke_ssr_pv","/fr/news/articles/some-slug"' in doc
    assert '"/news/articles/abc123"' not in doc


def test_doc_response_dedup_marker_falls_back_to_tracked_path() -> None:
    """Without an explicit dedup_path (every non-article route), the marker still uses tracked_path -- unchanged behavior."""
    resp = _doc_response(
        ("<title>t</title>", "<body-html>"),
        "public, max-age=300",
        tracked_path="/topics",
    )
    assert 'pxke_ssr_pv","/topics"' in resp.description


def test_reliable_tags_policy_and_section_redirect_map() -> None:
    # 50 stories: "market" on 25, "sdk" on 25 (both hit the 50% ubiquity
    # ceiling), "niche" on 10 (clears MIN_COUNT, kept), "borderline" on 9
    # (one short of MIN_COUNT, dropped).
    """Applies the ubiquity ceiling and MIN_COUNT floor to reliable_tags and maps every retired section."""
    items = _feed(50)
    for i in range(10):
        items[i].tags = [*items[i].tags, "niche"]
    for i in range(10, 19):
        items[i].tags = [*items[i].tags, "borderline"]
    picked = dict(reliable_tags(items))
    assert picked.get("niche") == 10
    assert "borderline" not in picked
    assert "market" not in picked
    assert "sdk" not in picked
    # Every retired section slug redirects to a topic.
    assert set(SECTION_REDIRECTS) == {"markets", "security", "developers", "community", "ecosystem"}


# --- article route: ?lang= consolidation ---------------------------------------


def _get_request(path_params: dict, query: dict) -> Request:
    return Request(
        method="GET",
        headers={},
        query_params=QueryParams(query),
        path_params=path_params,
    )


def test_article_lang_en_query_redirects_to_bare_canonical() -> None:
    """?lang=en used to fall through to a live 200 duplicate; it must 301 to the query-free URL like every other ?lang= form (GSC 'duplicate, Google chose different canonical' audit, 2026-08-23)."""
    resp = article(_get_request({"article_id": "some-slug"}, {"lang": "en"}))
    assert resp.status_code == 301
    assert resp.headers["Location"] == "/news/articles/some-slug"


def test_article_lang_unrecognized_query_redirects_to_bare_canonical() -> None:
    """A garbage/unrecognized ?lang= code must not serve a duplicate 200 either."""
    resp = article(_get_request({"article_id": "some-slug"}, {"lang": "xx"}))
    assert resp.status_code == 301
    assert resp.headers["Location"] == "/news/articles/some-slug"


# --- sitemap / robots --------------------------------------------------------


def test_robots_txt_points_to_sitemaps() -> None:
    """Points robots.txt at the main sitemap and disallows the admin path."""
    txt = sitemap.robots_txt()
    assert "Disallow: /admin" in txt
    assert "Sitemap: https://algorand.pxke.me/sitemap.xml" in txt


def test_robots_txt_blocks_api_but_allows_image_proxy() -> None:
    """The JSON API is crawl-budget waste (always noindex), but the image proxy behind og:image/hero images must stay fetchable."""
    txt = sitemap.robots_txt()
    assert "Disallow: /api/" in txt
    assert "Allow: /api/v1/img" in txt


def test_robots_txt_blocks_legacy_lang_query_param() -> None:
    """Legacy ?lang= article URLs all 301 to their canonical path form (2026-07-29 migration) -- blocking further crawl saves budget on a re-check with a predetermined outcome, without affecting real users (robots.txt doesn't touch the redirect itself)."""
    txt = sitemap.robots_txt()
    assert "Disallow: /*?lang=" in txt


def test_robots_news_sitemap_gated_by_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Includes or omits the news sitemap link in robots.txt based on the feature flag."""
    monkeypatch.setattr(sitemap.settings, "seo_news_sitemap_enabled", False)
    assert "sitemap-news.xml" not in sitemap.robots_txt()
    monkeypatch.setattr(sitemap.settings, "seo_news_sitemap_enabled", True)
    assert "sitemap-news.xml" in sitemap.robots_txt()


def test_robots_txt_host_dispatch_registry() -> None:
    """robots() serves registry_robots_txt(), pointed at the registry's own sitemap, when the request arrives on the registry's Host header."""
    from types import SimpleNamespace

    req = Request(
        method="GET",
        headers={},
        query_params=QueryParams({}),
        path_params={},
        url=SimpleNamespace(path="/robots.txt", host="algorand-registry.pxke.me", scheme="https"),
    )
    resp = robots(req)
    assert "Sitemap: https://algorand-registry.pxke.me/sitemap.xml" in resp.description
    assert "Sitemap: https://algorand.pxke.me/sitemap.xml" not in resp.description


def test_robots_txt_host_dispatch_x402() -> None:
    """robots() serves x402_robots_txt() on x402's Host header."""
    from types import SimpleNamespace

    req = Request(
        method="GET",
        headers={},
        query_params=QueryParams({}),
        path_params={},
        url=SimpleNamespace(path="/robots.txt", host="x402.pxke.me", scheme="https"),
    )
    resp = robots(req)
    assert "Sitemap: https://x402.pxke.me/sitemap.xml" in resp.description


def test_robots_txt_host_dispatch_default_is_news() -> None:
    """An unrecognized/default Host (the real news domain, or anything else) still gets the news robots.txt."""
    from types import SimpleNamespace

    req = Request(
        method="GET",
        headers={},
        query_params=QueryParams({}),
        path_params={},
        url=SimpleNamespace(path="/robots.txt", host="algorand.pxke.me", scheme="https"),
    )
    resp = robots(req)
    assert "Sitemap: https://algorand.pxke.me/sitemap.xml" in resp.description


def test_sitemap_xml_includes_articles_and_topics() -> None:
    # 25 stories -> "market" on 10 (clears MIN_COUNT, under the 50% ubiquity
    # ceiling), the rest singleton filler tags.
    """Includes article, topic and static page URLs in the sitemap, excluding retired sections."""
    xml = sitemap.sitemap_xml(_feed_with_reliable_topic("market", 10, 25))
    assert xml.startswith("<?xml")
    assert "https://algorand.pxke.me/news/articles/id0" in xml
    assert "https://algorand.pxke.me/topics" in xml
    assert "<loc>https://algorand.pxke.me/news</loc>" not in xml
    assert "https://algorand.pxke.me/hot" in xml
    assert "https://algorand.pxke.me/topic/market" in xml
    assert "/section/" not in xml


def test_sitemap_includes_image_extension_for_real_hero_images_only() -> None:
    """Emits <image:image> for a real hero image but skips the brand-icon/logo fallback."""
    items = _feed(2)
    items[0].image_url = "https://img.io/hero-photo.jpg"
    items[1].image_url = "https://img.io/algorand_logo_mark.png"
    xml = sitemap.sitemap_xml(items)
    assert 'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"' in xml
    assert "<image:loc>https://img.io/hero-photo.jpg</image:loc>" in xml
    assert "algorand_logo_mark.png" not in xml


def test_sitemap_article_hreflang_and_translations() -> None:
    """Includes hreflang alternates for an article's translations in the sitemap."""
    items = _feed(1)
    translations = {"id0": ["fa", "ar"]}
    xml = sitemap.sitemap_xml(items, translations)
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in xml
    assert 'hreflang="fa"' in xml
    assert 'hreflang="ar"' in xml
    assert "/fa/news/articles/" in xml
    assert "/ar/news/articles/" in xml
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
    assert "/fa/news/articles/" not in pages
    articles = build.parts["sitemap-articles-1.xml"]
    assert "/fa/news/articles/" in articles
    assert 'hreflang="fa"' in articles


def test_sitemap_single_file_when_under_limit() -> None:
    """Builds a single urlset with no index when under the URL cap."""
    build = sitemap.build_sitemaps(_feed(2), {})
    assert not build.is_index
    assert "<urlset" in build.root_xml
    assert "<sitemapindex" not in build.root_xml
    assert build.parts == {}


def test_sitemap_stays_single_file_for_many_light_articles() -> None:
    """A larger but untranslated (byte-light) feed stays a single urlset: neither cap is a hair-trigger on count alone."""
    items = _feed(45)
    build = sitemap.build_sitemaps(items, {})
    assert not build.is_index
    assert build.parts == {}
    # Sanity: comfortably under both caps, so this is a real "small" case,
    # not an accident of one cap being disabled.
    assert len(build.root_xml.encode("utf-8")) < sitemap.MAX_BYTES_PER_SITEMAP


def test_sitemap_splits_on_byte_size_despite_low_url_count() -> None:
    """A byte-heavy-but-few-URLs feed (full 8-language hreflang cluster per article) trips the byte-size cap and splits, even though the URL count stays far under MAX_URLS_PER_SITEMAP.

    This is the exact live bug: a count-only threshold never fires for this
    site because per-article translation fanout inflates bytes, not URL
    count. Uses the real MAX_BYTES_PER_SITEMAP default (no monkeypatch) so
    this exercises the production cap directly.
    """
    from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS

    items = _feed(45)
    translations = {f"id{i}": list(ARTICLE_TRANSLATION_LANGS) for i in range(45)}
    all_entries_count = len(sitemap._static_entries(items)) + len(
        sitemap._article_entries(items, translations)
    )
    assert all_entries_count < sitemap.MAX_URLS_PER_SITEMAP  # count check alone wouldn't split

    build = sitemap.build_sitemaps(items, translations)
    assert build.is_index
    assert "sitemap-pages.xml" in build.parts
    article_parts = [k for k in build.parts if k.startswith("sitemap-articles-")]
    assert len(article_parts) >= 2  # a sensible handful of chunks, not one file
    combined_articles = "".join(build.parts[name] for name in article_parts)
    assert 'hreflang="fa"' in combined_articles  # translations survive chunking


def test_chunk_splits_on_bytes_not_just_count() -> None:
    """`_chunk`'s greedy packer closes a chunk once EITHER the count or the byte budget would be exceeded by the next entry, and never drops or infinitely defers a single entry heavier than the byte budget."""
    small = [sitemap._UrlEntry(loc=f"https://x.io/{i}") for i in range(4)]
    one_entry_bytes = len(sitemap._url_xml(small[0]).encode("utf-8"))

    # Byte cap alone forces a split into pairs, well under a huge count cap.
    chunks = sitemap._chunk(small, max_count=1000, max_bytes=one_entry_bytes * 2)
    assert [len(c) for c in chunks] == [2, 2]

    # Count cap alone (huge byte budget) still behaves as a plain fixed-size chunker.
    chunks = sitemap._chunk(small, max_count=2, max_bytes=10_000_000)
    assert [len(c) for c in chunks] == [2, 2]

    # A single entry heavier than max_bytes on its own still gets its own
    # chunk rather than being dropped or merged past the budget.
    heavy = sitemap._UrlEntry(
        loc="https://x.io/heavy", alternates=[(f"l{i}", f"https://x.io/{i}") for i in range(20)]
    )
    chunks = sitemap._chunk([small[0], heavy, small[1]], max_count=1000, max_bytes=1)
    assert [len(c) for c in chunks] == [1, 1, 1]


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


def test_news_sitemap_caps_at_1000_urls() -> None:
    """Google News sitemap spec hard-caps at 1000 URLs — this site's daily publish cap keeps a 48h window far under that in practice, but the slice must still hold if it's ever exceeded."""
    import time

    many = _feed(1005, epoch=int(time.time()) - 3600)
    xml = sitemap.news_sitemap_xml(many)
    assert xml.count("<url>") == 1000


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


# --- registry / x402 sitemaps --------------------------------------------------


def _approved_project(slug: str = "acme", *, reviewed_at_epoch: int = 0) -> StoredProject:
    return StoredProject(
        slug=slug,
        name="Acme",
        domain="acme.test",
        url="https://acme.test",
        description="A test project for the registry sitemap.",
        status=STATUS_APPROVED,
        reviewed_at_epoch=reviewed_at_epoch,
    )


def test_build_registry_sitemap_includes_index_and_each_entry() -> None:
    """One <loc> for the registry root plus one per entry, on the registry's own domain."""
    xml = sitemap.build_registry_sitemap([_approved_project("acme"), _approved_project("beta")])
    assert xml.startswith("<?xml")
    assert "<loc>https://algorand-registry.pxke.me/</loc>" in xml
    assert "<loc>https://algorand-registry.pxke.me/registry/acme</loc>" in xml
    assert "<loc>https://algorand-registry.pxke.me/registry/beta</loc>" in xml
    # Never the news domain -- this is the whole point of registry_absolute().
    assert "algorand.pxke.me" not in xml


def test_build_registry_sitemap_omits_lastmod_when_never_reviewed() -> None:
    """A seeded/not-yet-reviewed entry (reviewed_at_epoch=0) gets no <lastmod>, rather than a fabricated 1970 date."""
    xml = sitemap.build_registry_sitemap([_approved_project("acme", reviewed_at_epoch=0)])
    entry_xml = xml.split("<loc>https://algorand-registry.pxke.me/registry/acme</loc>")[1]
    assert "<lastmod>" not in entry_xml.split("</url>")[0]


def _stored_listing(
    url: str = "https://svc.test/api", *, created_at_epoch: int = 0
) -> StoredListing:
    return StoredListing(
        url_hash="hash-1",
        url=url,
        price="1000",
        description="A test x402 listing.",
        schema_json="{}",
        settlement_tx_id="tx-1",
        term_end_epoch=9_999_999_999,
        created_at_epoch=created_at_epoch,
    )


def test_build_x402_sitemap_includes_static_pages_and_listings() -> None:
    """Directory/developers static pages plus one entry per listing, addressed by its own URL as a query param -- the live SPA's own addressing scheme, not a slug."""
    xml = sitemap.build_x402_sitemap([_stored_listing("https://svc.test/api")])
    assert "<loc>https://x402.pxke.me/</loc>" in xml
    assert "<loc>https://x402.pxke.me/directory</loc>" in xml
    assert "<loc>https://x402.pxke.me/developers</loc>" in xml
    assert "<loc>https://x402.pxke.me/listing?url=https%3A%2F%2Fsvc.test%2Fapi</loc>" in xml
    assert "algorand.pxke.me" not in xml
    assert "algorand-registry.pxke.me" not in xml


def _host_request(host: str, path: str = "/", *, path_params: dict | None = None) -> Request:
    from types import SimpleNamespace

    return Request(
        method="GET",
        headers={},
        query_params=QueryParams({}),
        path_params=path_params or {},
        url=SimpleNamespace(path=path, host=host, scheme="https"),
    )


def test_sitemap_root_host_dispatch_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """sitemap_root() serves the registry's own flat urlset, not the news index/chunk build, on the registry Host."""
    from app.modules.ecosystem.stores import factory as ecosystem_factory
    from app.modules.ecosystem.stores.memory import InMemoryProjectStore

    store = InMemoryProjectStore()
    store.upsert(_approved_project("acme"))
    ecosystem_factory.set_project_store(store)
    try:
        monkeypatch.setattr("app.core.cache.cached_json", lambda _key, _ttl, compute: compute())
        resp = sitemap_root(_host_request("algorand-registry.pxke.me", "/sitemap.xml"))
        assert "algorand-registry.pxke.me/registry/acme" in resp.description
        assert "algorand.pxke.me/news" not in resp.description
    finally:
        ecosystem_factory.set_project_store(None)


def test_sitemap_root_host_dispatch_x402(monkeypatch: pytest.MonkeyPatch) -> None:
    """sitemap_root() serves the x402 flat urlset on the x402 Host, reading live listings via ListingService directly (not the rate-limited HTTP route)."""
    from app.modules.x402_directory.services.listing_service import ListingService

    monkeypatch.setattr("app.core.cache.cached_json", lambda _key, _ttl, compute: compute())
    monkeypatch.setattr(
        ListingService, "search", lambda _self, **_kw: [_stored_listing("https://svc.test/api")]
    )
    resp = sitemap_root(_host_request("x402.pxke.me", "/sitemap.xml"))
    assert "x402.pxke.me/listing?url=" in resp.description
    assert "algorand.pxke.me" not in resp.description


def test_sitemap_root_default_host_is_still_the_news_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """The news domain (or anything unrecognized) keeps the existing index/chunk sitemap build untouched."""
    monkeypatch.setattr("app.core.cache.cached_json", lambda _key, _ttl, compute: compute())
    from app.modules.seo.api import routes as seo_routes

    monkeypatch.setattr(seo_routes.news, "list_feed_for_sitemap", lambda *, limit: ([], {}))  # noqa: ARG005 -- kwarg name must match the real signature (limit=...)
    resp = sitemap_root(_host_request("algorand.pxke.me", "/sitemap.xml"))
    assert "<loc>https://algorand.pxke.me/</loc>" in resp.description


def test_home_dispatches_to_registry_index_on_registry_host() -> None:
    """home() renders the registry index, not the news front page, when Host is the registry domain."""
    from app.modules.ecosystem.stores import factory as ecosystem_factory
    from app.modules.ecosystem.stores.memory import InMemoryProjectStore

    store = InMemoryProjectStore()
    store.upsert(_approved_project("acme"))
    ecosystem_factory.set_project_store(store)
    try:
        resp = home(_host_request("algorand-registry.pxke.me", "/"))
        assert resp.status_code == 200
        assert "Algorand Open Registry" in resp.description
        assert "Acme" in resp.description
    finally:
        ecosystem_factory.set_project_store(None)


def test_registry_entry_renders_an_approved_entry() -> None:
    """registry_entry() renders the entry's own SSR document for an approved slug."""
    from app.modules.ecosystem.stores import factory as ecosystem_factory
    from app.modules.ecosystem.stores.memory import InMemoryProjectStore

    store = InMemoryProjectStore()
    store.upsert(_approved_project("acme"))
    ecosystem_factory.set_project_store(store)
    try:
        resp = registry_entry(
            _host_request(
                "algorand-registry.pxke.me", "/registry/acme", path_params={"slug": "acme"}
            )
        )
        assert resp.status_code == 200
        assert "Acme" in resp.description
    finally:
        ecosystem_factory.set_project_store(None)


def test_registry_entry_404s_for_an_unknown_slug() -> None:
    """A slug with no store row (or a pending/rejected one) 404s with registry chrome, not the news 404."""
    from app.modules.ecosystem.stores import factory as ecosystem_factory
    from app.modules.ecosystem.stores.memory import InMemoryProjectStore

    ecosystem_factory.set_project_store(InMemoryProjectStore())
    try:
        req = _host_request(
            "algorand-registry.pxke.me", "/registry/nope", path_params={"slug": "nope"}
        )
        resp = registry_entry(req)
        assert resp.status_code == 404
    finally:
        ecosystem_factory.set_project_store(None)


def test_registry_index_route_reachable_directly_at_slash_registry() -> None:
    """/registry is its own registered route (not just '/' dispatch) -- exercised directly, same as a request proxied there from the news domain's redirect."""
    from app.modules.ecosystem.stores import factory as ecosystem_factory
    from app.modules.ecosystem.stores.memory import InMemoryProjectStore

    store = InMemoryProjectStore()
    store.upsert(_approved_project("acme"))
    ecosystem_factory.set_project_store(store)
    try:
        resp = registry_index(_host_request("algorand-registry.pxke.me", "/registry"))
        assert resp.status_code == 200
        assert "Acme" in resp.description
    finally:
        ecosystem_factory.set_project_store(None)


def test_registry_index_ignores_registry_host_for_the_news_domain() -> None:
    """home() on the ordinary news Host still renders the news front page, never the registry index."""
    resp = home(_host_request("algorand.pxke.me", "/"))
    assert resp.status_code == 200
    assert "Algorand Open Registry" not in resp.description


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
    doc = shell.render_document(head, body, html_lang="zh-Hans")
    assert doc is not None
    assert '<html lang="zh-Hans">' in doc
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
    dirs = shell._candidate_dirs("frontend_web")
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


def test_md_table_cell_with_glossary_link_renders_as_anchor() -> None:
    """A table cell carrying a glossary auto-link (real production shape, see glossary_linker.py) becomes a real anchor instead of leaking raw markdown into the cell — the <table> structure itself was always fine."""
    md = (
        "| Surface | Covers |\n"
        "|---|---|\n"
        '| Public API | Metadata for any [ASA](/glossary/asa "Algorand Standard Asset") |'
    )
    html = md_to_html(md)
    assert "<table>" in html
    assert "</table>" in html
    assert '<a href="https://algorand.pxke.me/glossary/asa"' in html
    assert "[ASA]" not in html


def test_md_duplicate_leading_image_collapses_to_one() -> None:
    """Writer-pipeline artifact: the body sometimes leads with the same hero image markdown twice back-to-back; the converter collapses an image-only paragraph that exactly repeats the previous one."""
    md = "![Title](https://img.io/hero.png)\n\n![Title](https://img.io/hero.png)\n\nBody text."
    html = md_to_html(md)
    assert html.count("<img") == 1
    assert "Body text." in html


def test_md_non_consecutive_repeated_image_is_kept() -> None:
    """Only an immediately-consecutive duplicate is collapsed — an image that legitimately reappears later in the body is left alone."""
    md = "![Title](https://img.io/hero.png)\n\nSome text in between.\n\n![Title](https://img.io/hero.png)"
    html = md_to_html(md)
    assert html.count("<img") == 2


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


def test_rss_full_content_image_link_and_table_all_render_correctly() -> None:
    """End-to-end regression for the three live RSS rendering bugs, using the real article shape confirmed against https://algorand.pxke.me/feed.xml (hero image, plain link, glossary auto-link, and a pipe table with a glossary link in a cell)."""
    body = (
        "![Pera Connect SDK](https://docs.perawallet.app/social-preview.png)\n\n"
        "## Overview\n\n"
        "See [Pera's site](https://perawallet.app/) and the "
        '[Algorand Standard Asset](/glossary/algorand-standard-asset "A token type") page.\n\n'
        "| Surface | Covers |\n"
        "|---|---|\n"
        '| Public API | Metadata for any [ASA](/glossary/asa "Algorand Standard Asset") |\n'
        '| Connect SDK | Wallet [connect](/glossary/connect "Wallet connection flow") calls |'
    )
    html = md_to_html(body)

    # 1. Image duplication: exactly one <img>, no leftover raw `![...]` syntax.
    assert html.count("<img") == 1
    assert "![" not in html

    # 2. Broken links: both the absolute and the glossary (relative + titled)
    #    link became real anchors; no raw `[text](url)` bracket syntax remains.
    assert '<a href="https://perawallet.app/" rel="noopener nofollow">Pera\'s site</a>' in html
    assert '<a href="https://algorand.pxke.me/glossary/algorand-standard-asset"' in html
    assert "](" not in html

    # 3. Malformed tables: a real <table> with <thead>/<tbody>, including the
    #    glossary link inside a cell converting to an anchor rather than
    #    leaving pipe/bracket soup in the feed text.
    assert "<table>" in html
    assert "</table>" in html
    assert "<th>Surface</th>" in html
    assert '<a href="https://algorand.pxke.me/glossary/asa"' in html
    assert "|" not in re.sub(r"<[^>]+>", "", html)

    xml = feeds.rss_xml(_feed(1), bodies={"id0": html})
    encoded = re.search(r"<content:encoded>(.*?)</content:encoded>", xml, re.S).group(1)
    # The XML-escaped payload round-trips the same markup (sanity check that
    # rss_xml doesn't itself mangle the already-correct HTML).
    assert "&lt;table&gt;" in encoded
    assert "&lt;img" in encoded
    assert '&lt;a href="https://perawallet.app/"' in encoded


def test_llms_txt_lists_feed_and_topics() -> None:
    """Lists the feed, sitemap and topics links in llms.txt, excluding retired sections."""
    txt = sitemap.llms_txt()
    assert txt.startswith("# PXke Algorand")
    assert "feed.xml" in txt
    assert "sitemap.xml" in txt
    assert "/topics" in txt
    assert "/feed/topic/" in txt
    assert "/section/" not in txt
    assert "llms-full.txt" in txt

    # API preconnect removed: feed/markets/auth are deferred; early preconnect
    # triggered Lighthouse "unused preconnect" and competed with WASM on boot.


def test_llms_full_txt_inlines_every_article_body() -> None:
    """Each item with a body gets its title/url/date/tags header plus the raw markdown body, in order, divided by a rule."""
    from app.modules.news.models.schemas import ArticleFeedItem

    items = [
        ArticleFeedItem(
            article_id="a1",
            service_id="svc-1",
            title="First Story",
            slug="first-story",
            summary="s1",
            tags=["defi", "algorand"],
            published_at_epoch=1735689600,
        ),
        ArticleFeedItem(
            article_id="a2",
            service_id="svc-2",
            title="Second Story",
            slug="second-story",
            summary="s2",
            tags=[],
            published_at_epoch=1735776000,
        ),
    ]
    bodies = {"a1": "# Heading\n\nBody one.", "a2": "Body two, no heading."}

    txt = sitemap.llms_full_txt(items, bodies)

    assert txt.startswith("# PXke Algorand")
    assert "## First Story" in txt
    assert "## Second Story" in txt
    assert "first-story" in txt
    assert "Tags: defi, algorand" in txt
    assert "Body one." in txt
    assert "Body two, no heading." in txt
    assert "---" in txt
    # Order preserved: First Story's block appears before Second Story's.
    assert txt.index("First Story") < txt.index("Second Story")


def test_llms_full_txt_skips_items_with_no_body() -> None:
    """An item with no fetched body (e.g. a store error) is silently skipped, not rendered as an empty section."""
    from app.modules.news.models.schemas import ArticleFeedItem

    items = [
        ArticleFeedItem(
            article_id="a1",
            service_id="svc-1",
            title="Has Body",
            slug="has-body",
            summary="s",
            tags=[],
            published_at_epoch=1735689600,
        ),
        ArticleFeedItem(
            article_id="a2",
            service_id="svc-2",
            title="No Body",
            slug="no-body",
            summary="s",
            tags=[],
            published_at_epoch=1735689600,
        ),
    ]
    txt = sitemap.llms_full_txt(items, {"a1": "Content."})
    assert "Has Body" in txt
    assert "No Body" not in txt


# --- title length budget + SSR visibility -------------------------------------


@pytest.mark.parametrize(
    ("title", "expect_brand_suffix", "expect_ellipsis"),
    [
        ("Short headline", True, False),
        ("Algorand Foundation Restructures Leadership For The Coming Years", False, False),
        (
            "Algorand Foundation Restructures Leadership to Accelerate "
            "AI-Driven On-Chain Activity Across the Entire Ecosystem",
            False,
            False,
        ),
        (
            "La Fondation Algorand restructure sa direction pour accélérer "
            "une activité on-chain pilotée par intelligence artificielle",
            False,
            False,
        ),
        (
            "阿尔戈兰德基金会重组领导层以加速整个生态系统中由人工智能驱动的链上活动增长",
            False,
            False,
        ),
    ],
    ids=["short", "near-budget", "overlong-en", "overlong-fr", "cjk-width"],
)
def test_title_budget(title: str, expect_brand_suffix: bool, expect_ellipsis: bool) -> None:
    """Title budget: short keeps brand suffix; long/CJK drop it; never blind-truncate mid-headline."""
    head, _ = render.render_article(_article(title=title))
    if expect_brand_suffix:
        assert f"<title>{title} — " in head
    else:
        assert f"<title>{title}</title>" in head
        assert f"{title} — " not in head
    if expect_ellipsis:
        assert "…</title>" in head
    else:
        assert "…</title>" not in head


def test_pathological_title_still_guarded() -> None:
    """Clamps only a runaway title, so a model glitch can never ship a multi-KB <title>."""
    t = "Algorand " * 60
    head, _ = render.render_article(_article(title=t))
    m = re.search(r"<title>(.*?)</title>", head)
    assert m is not None
    assert render._display_width(m.group(1)) <= 201
    assert m.group(1).endswith("…")


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


def test_icon_like_vs_real_share_image() -> None:
    """Icon/favicon URLs fall back to generated OG card; real heroes keep absolute + proxied img."""
    head, body = render.render_article(_article(image_url="https://brain-chain.app/favicon.svg"))
    assert "favicon.svg" not in head
    assert "favicon.svg" not in body
    assert 'property="og:image" content="https://algorand.pxke.me/og/article/' in head
    assert "<img" not in body

    head, body = render.render_article(_article(image_url="https://example.com/logo-dark.png"))
    assert "logo-dark.png" not in head
    assert "<img" not in body

    head, _ = render.render_article(_article(image_url="https://x.io/favicon.ico"))
    assert "/og/article/abc123.png" in head
    assert "favicon.ico" not in head

    head, _ = render.render_article(_article(image_url="https://img.io/h.png"))
    assert "og:image:width" not in head
    assert 'property="og:image:alt"' in head

    head, body = render.render_article(_article())
    assert "img.io/hero.png" in head
    assert '<img src="/api/v1/img?url=https%3A%2F%2Fimg.io%2Fhero.png"' in body


def test_icon_word_boundary_matching() -> None:
    """Matches icon-like filenames on word boundaries, not as bare substrings."""
    from app.modules.seo.render import is_icon_like

    assert is_icon_like("https://x.io/algorand_logo_mark_black-Feb.png")
    assert is_icon_like("https://x.io/valar-solutions-full-logo-preview.png")
    assert is_icon_like("https://x.io/apple-touch-icon.png")
    assert is_icon_like("https://x.io/anything.svg")
    assert not is_icon_like("https://x.io/silicon.png")
    assert not is_icon_like("https://x.io/features/hero-image.jpg")


def test_section_skips_provenance_tags_for_the_real_subject() -> None:
    """The section is what the story is ABOUT, never how we found it.

    "chain-only" and "discovery" are pipeline provenance — they say the story
    surfaced from an on-chain-only trigger, not that it concerns the chain.
    This backend used to treat "chain-only" as topical (its boilerplate list
    held 6 labels to the SPA's 18), so crawlers were told the section was
    "on-chain" while a reader saw "Payments" a frame after hydration. Both
    sides now read shared/taxonomy.json, so the subject wins on both.
    """
    head, _ = render.render_article(_article(tags=["chain-only", "discovery", "payments"]))
    # Display label ("Payments"), not the raw slug — same as every other
    # section/kicker in the suite (see the SDKs case above); only the
    # /topic/ URL below stays on the raw slug.
    assert 'property="article:section" content="Payments">' in head
    assert '"articleSection":"Payments"' in head
    assert "/topic/payments" in head


def test_display_label_rewrites_jargon_slugs_without_touching_urls() -> None:
    """Display text only: /topic/<tag> always links the raw slug, both sides."""
    assert topics.display_tag_label("chain-only") == "on-chain"
    assert topics.display_tag_label("CHAIN-ONLY") == "on-chain"
    assert topics.display_tag_label("payments") == "Payments"


def test_section_redirect_map_matches_the_spa() -> None:
    """The backend 301 map and the SPA's client-side copy must not drift.

    Both are live: nginx proxies /section/* here for the 301 (which is what
    Google follows), and App.svelte redirects the same paths client-side for a
    reader who arrives via in-app navigation. They cannot share code across the
    language boundary, so the only thing that can catch a one-sided edit is
    reading the other side's source.
    """
    import re
    from pathlib import Path

    app_svelte = Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.svelte"
    if not app_svelte.is_file():
        pytest.skip("frontend/ not present in this checkout")

    block = re.search(
        r"const SECTION_REDIRECTS:\s*Record<string,\s*string>\s*=\s*\{(.*?)\}",
        app_svelte.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert block, "SECTION_REDIRECTS not found in App.svelte — did it move or get renamed?"
    spa_map = dict(re.findall(r"(\w+)\s*:\s*'([^']+)'", block.group(1)))

    assert spa_map == SECTION_REDIRECTS


def test_article_path_prefers_the_slug_and_falls_back_to_the_id() -> None:
    """Slug URLs since migration 056; rows without one still resolve by id."""
    assert render.article_path("abc123", "my-story") == "/news/articles/my-story"
    assert render.article_path("abc123", None) == "/news/articles/abc123"


def test_article_urls_agree_across_canonical_feed_and_sitemap() -> None:
    """Canonical, RSS and sitemap must emit ONE url form — a mismatch reads as duplicate content."""
    from app.modules.seo.feeds import _article_path as feed_path

    article = _article(slug="my-story")
    head, _ = render.render_article(article)
    assert 'rel="canonical" href="https://algorand.pxke.me/news/articles/my-story"' in head
    assert feed_path(article.article_id, article.slug) == "/news/articles/my-story"


def test_slug_url_uses_the_locale_path_prefix() -> None:
    """A translated article canonicalises to the locale-prefixed slug path, not the id and not ?lang=."""
    head, _ = render.render_article(_article(slug="my-story"), lang="fa", translation_langs=["fa"])
    assert 'rel="canonical" href="https://algorand.pxke.me/fa/news/articles/my-story"' in head
    assert "?lang=" not in head
    assert "/news/articles/abc123" not in head


def test_sitemap_emits_slug_urls_not_ids() -> None:
    """Sitemap <loc> must match rel=canonical — a uuid here vs a slug there is duplicate content."""
    items = _feed(1)
    items[0].slug = "my-story"
    entries = sitemap._article_entries(items, {})
    locs = " ".join(e.loc for e in entries)
    assert "/news/articles/my-story" in locs
    assert "/news/articles/id0" not in locs


def test_beacon_accepts_slug_article_paths() -> None:
    """Article URLs are slugs since migration 056 — a uuid-only guard silently stopped counting reads."""
    assert _is_known_app_path("/news/articles/algorank-debuts-1k-project-directory")
    assert _is_known_app_path("/news/articles/afeeec91-dc1a-4cba-88b3-7447ac3ee2c3")
    # Still rejects junk, which is what the guard is for.
    assert not _is_known_app_path("/news/articles/Not A Slug")
    assert not _is_known_app_path("/news/articles/x/y")
    assert not _is_known_app_path("/news/articles/")
    assert not _is_known_app_path("/news/articles/" + "a" * 100)


def test_x402_endpoints_page_is_distinct_from_directory() -> None:
    """Regression for the 2026-09-06 frontend split.

    /x402/endpoints must SSR its own content, not silently fall back to the
    directory tab. Before this fix, "news" was removed from X402_TABS (the SPA dropped its
    own News tab the same way) but nothing added "endpoints" as a real page,
    so /x402/endpoints resolved through x402_tab's "unknown tab -> directory"
    fallback and served directory-listing content at a URL that should show
    PXke's own product catalog and the News Engine instead.
    """
    assert "news" not in render.X402_TABS, "News is a page section now, not a Marketplace sub-tab"
    assert "endpoints" not in render.X402_TABS, (
        "endpoints is its own page, not a Marketplace sub-tab"
    )

    directory_head, directory_body = render.render_x402("directory")
    news_items = [
        {"title": "A Headline", "url": "https://algorand.pxke.me/news/articles/a-headline"}
    ]
    endpoints_head, endpoints_body = render.render_x402("endpoints", news_items=news_items)

    assert "/x402/endpoints" in endpoints_head
    assert "/x402/endpoints" not in directory_head
    assert "A Headline" in endpoints_body
    assert "A Headline" not in directory_body
    assert render._X402_TAB_HEAD_TITLES["endpoints"] != render._X402_TAB_HEAD_TITLES["directory"]

    assert _is_known_app_path("/x402/endpoints")


def test_x402_ssr_news_pricing_matches_the_live_search_price() -> None:
    """Pins the SSR news pricing dl to the live search-price setting.

    The dl is a static mirror (render.py's own comment says so, not a live
    settings read) -- it drifted from the real price once already (article
    $0.01/search $0.02 shipped stale after the route went free at $0.001,
    caught by a marketing agent re-verifying the live page before this test
    existed). This pins the news rows so a future price change can't
    silently re-drift.
    """
    rows = dict(render._X402_PRICING_ROWS)
    assert rows["Read one news article"] == "free"
    assert rows["Search news articles"] == settings.x402_news_search_price
    assert "$0.001" in render._X402_TAB_DESCRIPTIONS["news"]
    assert "free" in render._X402_TAB_DESCRIPTIONS["news"]
