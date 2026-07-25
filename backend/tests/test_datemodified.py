"""dateModified plumbing: JSON-LD/meta + sitemap lastmod reflect updated_at.

Separate from test_seo.py so it stays runnable in environments without robyn
(render/sitemap import cleanly; only the routes module needs the web runtime).
"""

from __future__ import annotations

import re

from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.seo import render, sitemap


def _detail(**kw: object) -> ArticleDetail:
    base = {
        "article_id": "abc",
        "service_id": "svc",
        "title": "T",
        "summary": "S",
        "body": "B",
        "published_at_epoch": 1_750_000_000,
        "tags": ["sdk"],
    }
    base.update(kw)
    return ArticleDetail(**base)


def _mod_pub(head: str) -> tuple[str, str]:
    mod = re.search(r'"dateModified":"([^"]+)"', head).group(1)
    pub = re.search(r'"datePublished":"([^"]+)"', head).group(1)
    return mod, pub


def test_never_revised_article_has_modified_equal_published() -> None:
    """An article with no updated_at reports dateModified equal to datePublished."""
    head, _ = render.render_article(_detail())
    mod, pub = _mod_pub(head)
    assert mod == pub


def test_revised_article_advertises_revision_date() -> None:
    """An updated_at set on an article advertises a distinct dateModified and og:modified_time."""
    head, _ = render.render_article(_detail(updated_at_epoch=1_760_000_000))
    mod, pub = _mod_pub(head)
    assert mod != pub
    assert mod.startswith("2025-10")
    assert 'property="article:modified_time"' in head


def test_sitemap_lastmod_uses_updated_at() -> None:
    """Sitemap lastmod reflects updated_at_epoch when set, not just published_at_epoch."""
    items = [
        ArticleFeedItem(
            article_id="id0",
            service_id="s",
            title="T",
            summary="S",
            published_at_epoch=1_750_000_000,
            updated_at_epoch=1_760_000_000,
            tags=["sdk", "x"],
        ),
        ArticleFeedItem(
            article_id="id1",
            service_id="s",
            title="T2",
            summary="S",
            published_at_epoch=1_750_000_100,
            tags=["sdk", "x"],
        ),
    ]
    xml = sitemap.sitemap_xml(items)
    assert "2025-10-09" in xml
