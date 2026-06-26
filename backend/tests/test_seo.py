from __future__ import annotations

import json
import re

from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.seo import feeds, render, shell, sitemap
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
    assert "<noscript>" in body and "<h1>" in body


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
    assert "<pubDate>" in xml and "rel=\"self\"" in xml


# --- home / section ----------------------------------------------------------

def test_render_home_lists_articles() -> None:
    head, body = render.render_home(_feed(3))
    assert "CollectionPage" in head
    assert body.count("<li>") == 3


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
    "</head><body>\n<script src=\"flutter_bootstrap.js\" async></script></body></html>"
)


def test_shell_injection_dedups_and_keeps_bootstrap(monkeypatch, tmp_path) -> None:
    (tmp_path / "index.html").write_text(_SHELL, encoding="utf-8")
    monkeypatch.setattr(shell.settings, "frontend_dist_dir", str(tmp_path))
    shell._cache["html"] = None  # bust the module cache

    head, body = render.render_article(_article())
    doc = shell.render_document(head, body)
    assert doc is not None
    assert doc.count("<title>") == 1
    assert "Old Title" not in doc
    assert doc.count('property="og:title"') == 1
    assert doc.count('name="twitter:title"') == 1
    assert doc.count('rel="canonical"') == 1
    assert doc.count('type="application/rss+xml"') == 1
    assert "flutter_bootstrap.js" in doc
    assert "<noscript>" in doc
