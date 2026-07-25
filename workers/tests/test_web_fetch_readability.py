"""Readability-style main-content extraction in html_to_plain_text: the article body survives, boilerplate (nav/footer/cookie) is dropped, and degenerate pages fall back to whole-document text rather than returning nothing."""

from __future__ import annotations

from app.modules.scraper.core.web_fetch import html_to_plain_text

_ARTICLE_BODY = (
    "XBTO announced a strategic expansion into Algorand-based settlement, the "
    "company said in a filing. The move follows months of integration work, and "
    "it expands the firm's institutional custody footprint across several chains. "
    "Analysts noted the deal deepens liquidity for tokenized assets on the network."
)

_PAGE = f"""
<html><head><title>XBTO expands</title></head>
<body>
  <nav class="navbar">
    <a href="/home">Home</a><a href="/markets">Markets</a><a href="/about">About</a>
  </nav>
  <div id="cookie-consent">We use cookies. Accept all cookies to continue browsing.</div>
  <header><a href="/login">Log in</a><a href="/signup">Sign up</a></header>
  <article><p>{_ARTICLE_BODY}</p></article>
  <footer class="site-footer">
    Terms of Service · Privacy · © 2026 · Subscribe to our newsletter
  </footer>
</body></html>
"""


def test_article_body_kept_boilerplate_dropped() -> None:
    text = html_to_plain_text(_PAGE)
    assert "strategic expansion into Algorand-based settlement" in text
    # Boilerplate that previously ate the character budget is gone.
    assert "Accept all cookies" not in text
    assert "Terms of Service" not in text
    assert "Markets" not in text


def test_picks_content_div_without_semantic_article() -> None:
    page = f"""
    <html><body>
      <div class="menu"><a href="/a">One</a><a href="/b">Two</a><a href="/c">Three</a></div>
      <div class="post-body"><p>{_ARTICLE_BODY}</p><p>{_ARTICLE_BODY}</p></div>
    </body></html>
    """
    text = html_to_plain_text(page)
    assert "institutional custody footprint" in text
    assert "One" not in text
    assert "Two" not in text


def test_keep_links_renders_inline_urls() -> None:
    page = (
        "<html><body><article><p>"
        'Read the full proposal at <a href="https://algorand.foundation/gov">governance</a> '
        "before the vote closes, the foundation noted in its detailed update post."
        "</p></article></body></html>"
    )
    text = html_to_plain_text(page, keep_links=True)
    assert "governance (https://algorand.foundation/gov)" in text


def test_degenerate_page_falls_back() -> None:
    # No real content blocks → fall back to the whole cleaned document, not "".
    text = html_to_plain_text("<html><body><p>Short note about ALGO.</p></body></html>")
    assert "Short note about ALGO." in text
    assert html_to_plain_text("<html><body></body></html>") == ""
