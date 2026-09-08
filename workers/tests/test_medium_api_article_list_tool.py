"""medium_api_article_list reads a Medium author/publication/custom-domain RSS feed -- a non-feed 200 response must not read as a confirmed zero-article result."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai.research_tools import _tool_medium_articles

_RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Blog</title>
    <item>
      <title>First Post</title>
      <link>https://example.com/first</link>
      <pubDate>Mon, 01 Sep 2026 00:00:00 GMT</pubDate>
      <category>algorand</category>
    </item>
  </channel>
</rss>
"""


def test_a_real_feed_reports_its_articles(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ordinary path: a 200 RSS feed with a real <item>."""
    resp = httpx.Response(
        200,
        content=_RSS_FEED.encode(),
        request=httpx.Request("GET", "https://medium.com/feed/@author"),
    )
    monkeypatch.setattr("app.modules.ai.research_tools._guarded_get", lambda *_a, **_kw: resp)
    result = _tool_medium_articles("@author")
    assert result["count"] == 1
    assert result["articles"][0]["title"] == "First Post"


def test_a_200_domain_parking_page_reports_an_error_not_zero_articles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (2026-09-08, Fable audit, confirmed live against algonaut.space/feed -- the exact custom domain named in this tool's own docstring): a Medium-backed custom domain that stops serving RSS can 200 with an ordinary HTML page instead of 404ing. recover=True parses that into some tree with zero <item> elements -- that used to report count: 0, identical to a genuine empty feed."""
    html = "<!doctype html><html><head><title>algonaut.space - domain parking</title></head><body>For sale</body></html>"
    resp = httpx.Response(
        200, content=html.encode(), request=httpx.Request("GET", "https://algonaut.space/feed")
    )
    monkeypatch.setattr("app.modules.ai.research_tools._guarded_get", lambda *_a, **_kw: resp)
    result = _tool_medium_articles("algonaut.space")
    assert "error" in result
    assert result["articles"] == []
    assert "count" not in result


def test_a_genuinely_empty_real_feed_still_reports_zero_articles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real <rss><channel> with no <item>s (a brand-new blog) is a confirmed zero, not an error."""
    empty_feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>New Blog</title></channel></rss>'
    )
    resp = httpx.Response(
        200,
        content=empty_feed.encode(),
        request=httpx.Request("GET", "https://medium.com/feed/@newauthor"),
    )
    monkeypatch.setattr("app.modules.ai.research_tools._guarded_get", lambda *_a, **_kw: resp)
    result = _tool_medium_articles("@newauthor")
    assert result["count"] == 0
    assert "error" not in result


def test_medium_api_article_list_tool_registered() -> None:
    """Registers medium_api_article_list in both the tool schemas and handlers."""
    from app.modules.ai.research_tools import research_tools

    schemas, handlers = research_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "medium_api_article_list" in names
    assert "medium_api_article_list" in handlers
