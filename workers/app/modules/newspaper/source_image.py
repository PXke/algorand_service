"""Resolve a representative image for an article's source.

Profile/discovery stories often publish with no og:image, leaving the feed tile
and the social/OG card imageless. This fetches the source page (SSRF-guarded) and
returns its advertised share image, falling back to the largest brand icon — so
both the front-page tile and the shared card show the source's real artwork.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# validate(image_url, declaring_page_url) -> validated image URL or "".
# Anchored to the page that DECLARED the image, not the article's source_url:
# an og:image legitimately lives on the declaring page's own domain/CDN (a
# GitHub link cited in Sources advertises opengraph.githubassets.com — foreign
# to the article's subject site but correct for the declaring page).
_Validator = Callable[[str, str], str]

# Links that never carry a representative share image for the story subject:
# social/chat profiles. Content hosts (medium, youtube, news sites) stay in —
# their og:image is at least topical artwork.
_BODY_LINK_SKIP_HOSTS: frozenset[str] = frozenset(
    {
        "twitter.com",
        "x.com",
        "t.me",
        "discord.gg",
        "discord.com",
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "reddit.com",
        "bsky.app",
    }
)

_MD_LINK_RE = re.compile(r"\]\((https?://[^)\s]+)\)")
_SOURCES_HEADING_RE = re.compile(r"(?im)^#{1,6}\s*(sources?|references?)\b")


def homepage_from_service_id(service_id: str | None) -> str:
    """Reconstruct a source homepage from a service_id slug (dots slugified to dashes, e.g. "perawallet-app" -> "https://perawallet.app"). "" if implausible."""
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


def resolve_source_images(
    *,
    source_url: str | None,
    service_id: str | None,
    validate: _Validator | None = None,
) -> tuple[str, str]:
    """(og_image, logo_image): the first share image and first brand icon found across the candidate URLs. Best-effort — returns ("", "") on any failure.

    When ``validate`` is given, every candidate is validated BEFORE being
    accepted, so a declared-but-dead og:image (404 artwork, hung IPFS gateway)
    can't short-circuit the search — root-caused 2026-07-16: aramid.finance
    declares og/twitter images that both 404 and subtopia.io's og:image sits
    on the dead nftstorage.link gateway; both articles published imageless
    while their cited Sources links advertised perfectly fetchable images
    that this early-stop skipped and the caller's post-hoc validation could
    no longer recover.
    """
    og, logo = "", ""
    for url in candidate_urls(source_url=source_url, service_id=service_id):
        try:
            page_og, page_logo = _images_from_url(url)
        except Exception as exc:
            log.info("source image fetch failed for %s: %s", url, exc)
            continue
        if validate is not None:
            page_og = validate(page_og, url) if page_og else ""
            page_logo = validate(page_logo, url) if page_logo else ""
        og = og or page_og
        logo = logo or page_logo
        if og:  # a real share image is the best result — stop early
            break
    return og, logo


def source_urls_from_body(body: str, limit: int = 4) -> list[str]:
    """Cited http(s) links from the article's Sources/References section (the writer appends every fetched research URL there — reference_block.py), falling back to all body links when no such section exists. This is the image path for lanes whose source_url isn't fetchable (editorial://brief/…, mail://message/…) and whose service_id isn't a domain slug."""
    text = body or ""
    heading = _SOURCES_HEADING_RE.search(text)
    if heading:
        text = text[heading.end() :]
    urls: list[str] = []
    seen: set[str] = set()
    for url in _MD_LINK_RE.findall(text):
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if not host or host in _BODY_LINK_SKIP_HOSTS or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def resolve_article_images(
    *,
    source_url: str | None,
    service_id: str | None,
    body: str = "",
    validate: _Validator | None = None,
) -> tuple[str, str]:
    """resolve_source_images, then fall back to the article's own cited links.

    Best-effort — returns ("", "") when nothing anywhere advertises an image.

    Pass ``validate`` so dead declared images are rejected mid-search and the
    cited-links fallback still runs (see resolve_source_images).
    """
    og, logo = resolve_source_images(
        source_url=source_url, service_id=service_id, validate=validate
    )
    if og:
        return og, logo
    for url in source_urls_from_body(body):
        try:
            page_og, page_logo = _images_from_url(url)
        except Exception as exc:
            log.info("source image fetch failed for %s: %s", url, exc)
            continue
        if validate is not None:
            page_og = validate(page_og, url) if page_og else ""
            page_logo = validate(page_logo, url) if page_logo else ""
        og = og or page_og
        logo = logo or page_logo
        if og:
            break
    return og, logo
