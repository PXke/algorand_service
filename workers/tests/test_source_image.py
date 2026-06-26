from __future__ import annotations

from bs4 import BeautifulSoup

from app.modules.newspaper.source_image import candidate_urls, homepage_from_service_id
from app.modules.scraper.core.page_metadata import extract_og_image, extract_source_logo

_PERA_HEAD = """
<html><head>
  <meta property="og:image" content="https://perawallet.s3.amazonaws.com/images/pera-header.png"/>
  <link rel="apple-touch-icon" sizes="57x57" href="/images/favicon/apple-icon-57x57.png"/>
  <link rel="apple-touch-icon" sizes="180x180" href="/images/favicon/apple-icon-180x180.png"/>
  <link rel="icon" sizes="192x192" href="/images/favicon/android-icon-192x192.png"/>
  <link rel="icon" sizes="16x16" href="/images/favicon/favicon-16x16.png"/>
</head><body></body></html>
"""


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_extract_og_image_absolute() -> None:
    og = extract_og_image(_soup(_PERA_HEAD), "https://perawallet.app/")
    assert og == "https://perawallet.s3.amazonaws.com/images/pera-header.png"


def test_extract_source_logo_picks_largest_absolute() -> None:
    logo = extract_source_logo(_soup(_PERA_HEAD), "https://perawallet.app/")
    # 192x192 is the biggest declared icon, resolved to an absolute URL.
    assert logo == "https://perawallet.app/images/favicon/android-icon-192x192.png"


def test_logo_empty_when_no_icons() -> None:
    assert extract_source_logo(_soup("<html><head></head></html>"), "https://x.io") == ""


def test_homepage_from_service_id() -> None:
    assert homepage_from_service_id("perawallet-app") == "https://perawallet.app"
    assert homepage_from_service_id("app-folks-finance") == "https://app.folks.finance"
    assert homepage_from_service_id("weekly-digest") == "https://weekly.digest"  # plausible-looking
    assert homepage_from_service_id("") == ""
    assert homepage_from_service_id("nodash") == ""  # no dash -> not a domain slug


def test_candidate_urls_order() -> None:
    # Source URL first (contextual), then the brand homepage.
    urls = candidate_urls(source_url="https://blog.x.io/post", service_id="x-io")
    assert urls == ["https://blog.x.io/post", "https://x.io"]
    # No source URL -> just the homepage.
    assert candidate_urls(source_url=None, service_id="perawallet-app") == [
        "https://perawallet.app"
    ]
    # Neither -> empty.
    assert candidate_urls(source_url="", service_id="") == []
