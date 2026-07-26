"""Stripping boilerplate from HTML-to-plain-text conversion."""

from __future__ import annotations

from app.modules.scraper.core.web_fetch import html_to_plain_text


def test_html_to_plain_text_removes_common_boilerplate() -> None:
    """Strips header/nav/cookie-banner/footer boilerplate while keeping the real article content."""
    html = """
    <html>
      <body>
        <header>Global Header</header>
        <nav class="main-menu">Main Menu</nav>
        <div id="cookie-banner">We use cookies</div>
        <main>
          <h1>Launch Update</h1>
          <p>Algorand ecosystem update with core roadmap details.</p>
        </main>
        <footer>Footer Links</footer>
      </body>
    </html>
    """
    text = html_to_plain_text(html)
    assert "Launch Update" in text
    assert "core roadmap details" in text
    assert "Global Header" not in text
    assert "Main Menu" not in text
    assert "We use cookies" not in text
    assert "Footer Links" not in text
