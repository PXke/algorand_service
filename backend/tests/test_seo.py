from __future__ import annotations

import json
import re

from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.seo import feeds, render, shell, sitemap
from app.modules.seo.api.routes import _is_known_app_path
from app.modules.seo.markdown import md_to_html, md_to_text, truncate
from app.modules.seo.sections import matches_section, section_for_slug


def _article(**kw) -> ArticleDetail:
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
    html = md_to_html("# Title\n\nHello **bold** and *em* and `code`.\n\n- a\n- b")
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>em</em>" in html
    assert "<code>code</code>" in html
    assert "<ul>" in html and "<li>a</li>" in html


def test_md_link_is_escaped_and_nofollow() -> None:
    html = md_to_html("See [click](https://e.x/p?q=1).")
    assert 'href="https://e.x/p?q=1"' in html
    assert 'rel="noopener nofollow"' in html


def test_md_escapes_html() -> None:
    html = md_to_html("a <script>alert(1)</script> b")
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_md_to_text_strips_markup() -> None:
    text = md_to_text("# H\n\n**bold** [x](https://a.b) `c`")
    assert "#" not in text and "*" not in text and "`" not in text
    assert "bold" in text and "x" in text


def test_truncate_word_boundary() -> None:
    out = truncate("one two three four five", 12)
    assert len(out) <= 13 and out.endswith("…")


# --- article rendering -------------------------------------------------------


def test_render_article_head_has_core_tags() -> None:
    head, body = render.render_article(_article())
    assert head.count("<title>") == 1
    assert 'rel="canonical" href="https://algorand.pxke.me/news/articles/abc123"' in head
    assert 'property="og:type" content="article"' in head
    assert "img.io/hero.png" in head
    assert 'name="twitter:card" content="summary_large_image"' in head
    assert 'id="ssr-body"' in body and "<h1>" in body
    # Visible SSR content, NOT noscript (Google renders JS and ignores noscript,
    # and the canvas Flutter app has no DOM text) + self-removal on app paint.
    assert "<noscript>" not in body
    assert "flutter-first-frame" in body


def test_render_article_hreflang_for_translations() -> None:
    head, _ = render.render_article(
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


def test_render_article_jsonld_is_valid_newsarticle() -> None:
    head, _ = render.render_article(_article())
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', head, re.DOTALL)
    assert m
    data = json.loads(m.group(1).replace("<\\/", "</"))
    assert data["@type"] == "NewsArticle"
    assert data["headline"]
    assert data["datePublished"].startswith("20")
    assert data["articleBody"]
    assert data["publisher"]["name"] == "PXke Algorand"


def test_render_article_falls_back_to_default_image() -> None:
    head, _ = render.render_article(_article(image_url=None))
    assert "/icons/Icon-512.png" in head


def test_render_noindex_marks_robots() -> None:
    head, _ = render.render_noindex("Admin")
    assert 'name="robots" content="noindex, follow"' in head


def test_article_has_breadcrumb_and_image_meta() -> None:
    head, _ = render.render_article(_article(image_url=None, tags=["sdk"]))
    assert '"@type":"BreadcrumbList"' in head
    assert '"name":"Developers"' in head  # section crumb from the "sdk" tag
    # default image -> known dimensions + alt on both og and twitter
    assert 'property="og:image:width" content="512"' in head
    assert 'property="og:image:alt"' in head and 'name="twitter:image:alt"' in head


def test_article_real_image_omits_dimensions() -> None:
    head, _ = render.render_article(_article(image_url="https://img.io/h.png"))
    assert "og:image:width" not in head  # unknown dims for a real hero image
    assert 'property="og:image:alt"' in head  # alt still present


def test_head_has_rss_alternate_link() -> None:
    head, _ = render.render_home(_feed(2))
    assert 'type="application/rss+xml"' in head
    assert "/feed.xml" in head


def test_home_has_website_searchaction_and_org(monkeypatch) -> None:
    monkeypatch.setattr(render.settings, "seo_same_as", "https://x.com/pxke")
    head, _ = render.render_home(_feed(2))
    assert '"@type":"WebSite"' in head and '"@type":"SearchAction"' in head
    assert "/search?q={search_term_string}" in head
    assert '"sameAs":["https://x.com/pxke"]' in head


# --- RSS feed ----------------------------------------------------------------


def test_rss_feed_structure() -> None:
    xml = feeds.rss_xml(_feed(3))
    assert xml.startswith("<?xml")
    assert '<rss version="2.0"' in xml and "<channel>" in xml
    assert xml.count("<item>") == 3
    assert "https://algorand.pxke.me/news/articles/id0" in xml
    assert "<pubDate>" in xml and 'rel="self"' in xml


# --- home / section ----------------------------------------------------------


def test_render_home_lists_articles() -> None:
    head, body = render.render_home(_feed(3))
    assert "CollectionPage" in head
    assert 'id="pxke-ssr-feed"' in head
    assert '"items":' in head
    assert body.count("<li>") == 3


def test_beacon_path_validation_rejects_made_up_paths() -> None:
    # Known static routes and a real section slug pass.
    assert _is_known_app_path("/")
    assert _is_known_app_path("/news")
    assert _is_known_app_path("/about")
    assert _is_known_app_path("/section/markets")
    assert _is_known_app_path("/news/articles/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d")
    # Admin isn't a public pageview route, a bogus section doesn't exist, and a
    # non-UUID "article id" or arbitrary path must not bump analytics counters.
    assert not _is_known_app_path("/admin")
    assert not _is_known_app_path("/section/does-not-exist")
    assert not _is_known_app_path("/news/articles/not-a-uuid")
    assert not _is_known_app_path("/random/garbage")


def test_ssr_track_snippet_marks_recorded_path() -> None:
    snippet = shell.ssr_track_snippet('/news/articles/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d')
    assert 'sessionStorage.setItem("pxke_ssr_pv"' in snippet
    assert "/news/articles/9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d" in snippet


def test_section_lookup_and_matching() -> None:
    sec = section_for_slug("developers")
    assert sec is not None
    assert matches_section(sec, ["sdk"])
    assert not matches_section(sec, ["market"])


# --- sitemap / robots --------------------------------------------------------


def test_robots_txt_points_to_sitemaps() -> None:
    txt = sitemap.robots_txt()
    assert "Disallow: /admin" in txt
    assert "Sitemap: https://algorand.pxke.me/sitemap.xml" in txt


def test_robots_news_sitemap_gated_by_flag(monkeypatch) -> None:
    monkeypatch.setattr(sitemap.settings, "seo_news_sitemap_enabled", False)
    assert "sitemap-news.xml" not in sitemap.robots_txt()
    monkeypatch.setattr(sitemap.settings, "seo_news_sitemap_enabled", True)
    assert "sitemap-news.xml" in sitemap.robots_txt()


def test_sitemap_xml_includes_articles_and_sections() -> None:
    xml = sitemap.sitemap_xml(_feed(2))
    assert xml.startswith("<?xml")
    assert "https://algorand.pxke.me/news/articles/id0" in xml
    assert "https://algorand.pxke.me/section/markets" in xml


def test_sitemap_article_hreflang_and_translations() -> None:
    items = _feed(1)
    translations = {"id0": ["fa", "ar"]}
    xml = sitemap.sitemap_xml(items, translations)
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in xml
    assert 'hreflang="fa-AF"' in xml
    assert 'hreflang="ar"' in xml
    assert "?lang=fa" in xml
    assert "?lang=ar" in xml
    assert 'hreflang="x-default"' in xml


def test_sitemap_splits_into_index_when_large(monkeypatch) -> None:
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
    assert "/section/markets" in pages
    assert "?lang=fa" not in pages
    articles = build.parts["sitemap-articles-1.xml"]
    assert "?lang=fa" in articles
    assert 'hreflang="fa-AF"' in articles


def test_sitemap_single_file_when_under_limit() -> None:
    build = sitemap.build_sitemaps(_feed(2), {})
    assert not build.is_index
    assert "<urlset" in build.root_xml
    assert "<sitemapindex" not in build.root_xml
    assert build.parts == {}


def test_news_sitemap_windows_recent_only() -> None:
    import time

    fresh = _feed(2, epoch=int(time.time()) - 3600)
    xml = sitemap.news_sitemap_xml(fresh)
    assert "news:news" in xml and "news:publication_date" in xml
    old = sitemap.news_sitemap_xml(_feed(2, epoch=1_700_000_000))
    assert "<url>" not in old


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


def test_shell_injection_dedups_and_keeps_bootstrap(monkeypatch, tmp_path) -> None:
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
    assert doc.count('type="application/rss+xml"') == 1
    assert "flutter_bootstrap.js" in doc
    assert 'id="ssr-body"' in doc


def test_shell_injection_preserves_jsonld_escapes(monkeypatch, tmp_path) -> None:
    """The head must be injected verbatim: re.sub replacement-string escape
    processing turned the \\n sequences inside the JSON-LD articleBody into raw
    newlines — invalid JSON that made Google drop the NewsArticle block."""
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
    head, _ = render.render_article(_article())
    assert 'name="robots" content="max-image-preview:large"' in head
    # noindex pages keep their explicit directive untouched
    head, _ = render.render_noindex("Admin")
    assert 'content="noindex, follow"' in head
    assert "max-image-preview" not in head


# --- markdown tables / fences --------------------------------------------------


def test_md_table_renders_as_html_table() -> None:
    md = "| Wallet | Type |\n|--------|------|\n| Pera | Mobile |\n| Defly | Mobile |"
    html = md_to_html(md)
    assert "<table>" in html and "</table>" in html
    assert "<th>Wallet</th>" in html
    assert "<td>Pera</td>" in html
    assert "|" not in re.sub(r"<[^>]+>", "", html)  # no pipe soup in the text


def test_md_chart_fence_becomes_caption_not_json() -> None:
    md = (
        "Before.\n\n```chart\n"
        '{"type": "bar", "title": "TVL by protocol", "x": ["a"]}\n'
        "```\n\nAfter."
    )
    html = md_to_html(md)
    assert '"type"' not in html and "```" not in html
    assert "Chart: TVL by protocol" in html
    text = md_to_text(md)
    assert '"type"' not in text and "TVL by protocol" not in text
    assert "Before." in text and "After." in text


def test_md_generic_fence_renders_as_code_block() -> None:
    html = md_to_html("```python\nprint('hi')\n```")
    assert "<pre><code>" in html
    assert "print(" in html


def test_md_to_text_flattens_tables() -> None:
    text = md_to_text("| A | B |\n|---|---|\n| 1 | 2 |")
    assert "|" not in text and "---" not in text
    assert "A" in text and "1" in text


# --- full-content RSS / llms.txt ------------------------------------------------


def test_rss_full_content_encoded() -> None:
    items = _feed(2)
    xml = feeds.rss_xml(items, bodies={"id0": "<p>Full body zero</p>"})
    assert 'xmlns:content="http://purl.org/rss/1.0/modules/content/"' in xml
    assert "&lt;p&gt;Full body zero&lt;/p&gt;" in xml
    assert xml.count("content:encoded") == 2  # open+close for the one item with a body


def test_llms_txt_lists_feed_and_sections() -> None:
    txt = sitemap.llms_txt()
    assert txt.startswith("# PXke Algorand")
    assert "feed.xml" in txt and "sitemap.xml" in txt
    assert "/section/markets" in txt


def test_shell_injection_adds_engine_preloads(monkeypatch, tmp_path) -> None:
    (tmp_path / "index.html").write_text(_SHELL, encoding="utf-8")
    monkeypatch.setattr(shell.settings, "frontend_dist_dir", str(tmp_path))
    shell._cache["html"] = None  # bust the module cache

    head, body = render.render_home(_feed(1))
    doc = shell.render_document(head, body)
    assert doc is not None
    # Renderer preloads are injected by an inline script mirroring
    # flutter_bootstrap.js's build selector (one stack per browser — skwasm OR
    # canvaskit, never both). The wasm branch must gate on all three of the
    # selector's conditions: Chromium/Blink (Flutter's default wasm allowlist
    # is blink-only — Firefox has WasmGC yet runs dart2js), WasmGC, and WebGL.
    assert "WebAssembly.validate" in doc
    assert "cr&&gc&&gl" in doc
    assert "main.dart.mjs" in doc
    assert "skwasm.wasm" in doc
    assert "canvaskit.wasm" in doc
    # API preconnect removed: feed/markets/auth are deferred; early preconnect
    # triggered Lighthouse "unused preconnect" and competed with WASM on boot.


# --- title length budget + SSR visibility -------------------------------------


def test_short_title_keeps_brand_suffix() -> None:
    head, _ = render.render_article(_article(title="Short headline"))
    assert "<title>Short headline — " in head


def test_long_title_drops_brand_suffix_but_stays_whole() -> None:
    t = "Algorand Foundation Restructures Leadership For The Coming Years"  # 65 chars
    head, _ = render.render_article(_article(title=t))
    assert f"<title>{t}</title>" in head


def test_overlong_title_clamped_at_word_boundary() -> None:
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


def test_first_frame_script_keeps_content_visible() -> None:
    # aria-hidden only: search engines' renderers reach flutter-first-frame,
    # and display:none'd main content is devalued in the rendered snapshot.
    # The engine's own full-viewport canvas already covers the SSR body.
    _, body = render.render_article(_article())
    assert "aria-hidden" in body
    assert "display='none'" not in body and "e.remove()" not in body


# --- icon-like image_url must not become a hero or share image -----------------


def test_favicon_image_url_never_becomes_hero_or_og_image() -> None:
    head, body = render.render_article(
        _article(image_url="https://brain-chain.app/favicon.svg")
    )
    assert "favicon.svg" not in head and "favicon.svg" not in body
    # Metas fall back to the site default share image instead.
    assert 'property="og:image" content="https://algorand.pxke.me/icons/' in head
    # No stretched-icon hero in the SSR body.
    assert "<img" not in body


def test_icon_named_png_also_treated_as_icon() -> None:
    head, body = render.render_article(
        _article(image_url="https://example.com/logo-dark.png")
    )
    assert "logo-dark.png" not in head and "<img" not in body


def test_real_share_image_still_renders_hero() -> None:
    head, body = render.render_article(_article())
    assert "img.io/hero.png" in head
    assert '<img src="https://img.io/hero.png"' in body


def test_icon_word_boundary_matching() -> None:
    from app.modules.seo.render import _is_icon_like

    assert _is_icon_like("https://x.io/algorand_logo_mark_black-Feb.png")
    assert _is_icon_like("https://x.io/valar-solutions-full-logo-preview.png")
    assert _is_icon_like("https://x.io/apple-touch-icon.png")
    assert _is_icon_like("https://x.io/anything.svg")
    assert not _is_icon_like("https://x.io/silicon.png")
    assert not _is_icon_like("https://x.io/features/hero-image.jpg")
