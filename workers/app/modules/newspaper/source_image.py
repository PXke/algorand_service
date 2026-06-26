"""Resolve a representative image for an article's source.

Profile/discovery stories often publish with no og:image, leaving the feed tile
and the social/OG card imageless. This fetches the source page (SSRF-guarded) and
returns its advertised share image, falling back to the largest brand icon — so
both the front-page tile and the shared card show the source's real artwork.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


def homepage_from_service_id(service_id: str | None) -> str:
    """Reconstruct a source homepage from a service_id slug (dots slugified to
    dashes, e.g. "perawallet-app" -> "https://perawallet.app"). "" if implausible."""
    sid = (service_id or "").strip().lower()
    if not sid or "-" not in sid or not re.fullmatch(r"[a-z0-9.-]+", sid):
        return ""
    return "https://" + sid.replace("-", ".")


def candidate_urls(*, source_url: str | None, service_id: str | None) -> list[str]:
    """Source page first (contextual image), then the brand homepage."""
    urls: list[str] = []
    if source_url and source_url.lower().startswith(("http://", "https://")):
        urls.append(source_url)
    home = homepage_from_service_id(service_id)
    if home and home not in urls:
        urls.append(home)
    return urls


def _images_from_url(url: str) -> tuple[str, str]:
    from bs4 import BeautifulSoup

    from app.core.net_guard import guarded_get
    from app.modules.scraper.core.page_metadata import extract_og_image, extract_source_logo

    resp = guarded_get(url, headers={"Accept": "text/html"}, timeout=8.0)
    resp.raise_for_status()
    final = str(resp.url)
    soup = BeautifulSoup(resp.text, "html.parser")
    return extract_og_image(soup, final), extract_source_logo(soup, final)


def resolve_source_images(*, source_url: str | None, service_id: str | None) -> tuple[str, str]:
    """(og_image, logo_image): the first share image and first brand icon found
    across the candidate URLs. Best-effort — returns ("", "") on any failure."""
    og, logo = "", ""
    for url in candidate_urls(source_url=source_url, service_id=service_id):
        try:
            page_og, page_logo = _images_from_url(url)
        except Exception as exc:
            log.info("source image fetch failed for %s: %s", url, exc)
            continue
        og = og or page_og
        logo = logo or page_logo
        if og:  # a real share image is the best result — stop early
            break
    return og, logo
