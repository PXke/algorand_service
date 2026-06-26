"""Hero image extraction: ONLY the advertised social meta (og/twitter) image,
returned absolute. Inner content <img> is deliberately NOT used (misuse)."""

from bs4 import BeautifulSoup

from app.modules.scraper.core.page_metadata import extract_og_image


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_prefers_og_image_absolute() -> None:
    s = _soup('<meta property="og:image" content="/img/hero.png">')
    assert extract_og_image(s, "https://x.com/a") == "https://x.com/img/hero.png"


def test_twitter_image_fallback() -> None:
    s = _soup('<meta name="twitter:image" content="https://cdn.x.com/t.jpg">')
    assert extract_og_image(s) == "https://cdn.x.com/t.jpg"


def test_no_meta_returns_empty_even_with_content_images() -> None:
    # Inner <img> must NOT be used — only advertised social meta counts.
    s = _soup('<img src="https://cdn.x.com/photo.jpg" width="800" height="400">')
    assert extract_og_image(s, "https://x.com") == ""
