from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

# Where a publish date hides, in priority order (meta property/name → content).
_PUBLISHED_META = (
    ("property", "article:published_time"),
    ("property", "og:article:published_time"),
    ("name", "article:published_time"),
    ("itemprop", "datePublished"),
    ("name", "datePublished"),
    ("name", "publish-date"),
    ("name", "pubdate"),
    ("name", "date"),
    ("property", "og:updated_time"),
    ("name", "article:modified_time"),
    ("property", "article:modified_time"),
)


def _meta_content(soup, attr: str, value: str) -> str:
    tag = soup.find("meta", attrs={attr: value})
    if tag and tag.get("content"):
        return str(tag["content"]).strip()
    return ""


# Mirrors the frontend's looksLikeLogoUrl (app_config.dart) — a site's own
# og:image sometimes points at its favicon or a generic OG-generator endpoint
# rather than real share artwork (e.g. a-wallet.net declares og:image =
# /favicon.ico while a real twitter:image sits right below it, 2026-07-14).
# Skip such candidates so a later, better meta tag gets a chance instead of
# accepting the first hit uncritically.
_LOGO_SHAPED_RE = re.compile(
    r"favicon|apple-touch|/icons?[/._-]|[/._-]icons?[._-]|logo|/og(/|$)|opengraph"
)


def _looks_like_logo_url(url: str) -> bool:
    from urllib.parse import urlparse

    path = urlparse(url).path.lower()
    if path.endswith((".svg", ".ico")):
        return True
    return bool(_LOGO_SHAPED_RE.search(path))


def extract_og_image(soup, base_url: str = "") -> str:
    """The page's explicitly-advertised social/share image → absolute URL, or "".

    ONLY the social meta tags (og:image / twitter:image / og:image:url /
    twitter:image:src) — these are the images the site published FOR sharing. We
    deliberately do NOT scrape inner content <img> tags as a fallback: those
    aren't meant to represent the page and reusing them would be a misuse."""
    from urllib.parse import urljoin

    for attrs in (
        {"property": "og:image"},
        {"name": "twitter:image"},
        {"property": "og:image:url"},
        {"name": "twitter:image:src"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            candidate = urljoin(base_url, str(tag["content"]).strip())
            if not _looks_like_logo_url(candidate):
                return candidate
    return ""


def extract_source_logo(soup, base_url: str = "") -> str:
    """Largest declared site icon (apple-touch-icon / rel=icon) → absolute URL.

    A brand-logo fallback for when a page advertises no og:image. Picks the
    biggest `sizes`, preferring apple-touch-icons (usually the cleanest mark)."""
    from urllib.parse import urljoin

    best_url, best_score = "", 0
    for link in soup.find_all("link", rel=True):
        rel = link.get("rel")
        rels = " ".join(rel).lower() if isinstance(rel, list) else str(rel).lower()
        if "icon" not in rels:
            continue
        href = link.get("href")
        if not href:
            continue
        sizes = str(link.get("sizes", "")).lower()
        m = re.search(r"(\d+)x(\d+)", sizes)
        size = int(m.group(1)) if m else (180 if "apple-touch-icon" in rels else 16)
        # Slightly favour apple-touch-icons at equal declared size.
        score = size * 10 + (5 if "apple-touch-icon" in rels else 0)
        if score > best_score:
            best_score, best_url = score, urljoin(base_url, str(href).strip())
    return best_url


def _jsonld_date(soup) -> str:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for obj in data if isinstance(data, list) else [data]:
            if isinstance(obj, dict):
                for key in ("datePublished", "dateCreated", "dateModified"):
                    val = obj.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
    return ""


def extract_published_at(soup) -> str:
    """Best-effort ISO publish date for a page; "" when none is present."""
    for attr, value in _PUBLISHED_META:
        found = _meta_content(soup, attr, value)
        if found:
            return found
    jsonld = _jsonld_date(soup)
    if jsonld:
        return jsonld
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag and time_tag.get("datetime"):
        return str(time_tag["datetime"]).strip()
    return ""


def extract_page_meta(soup) -> dict[str, str]:
    """Extra OG/meta attributes worth keeping: attribution + categorisation."""
    meta: dict[str, str] = {}
    site_name = _meta_content(soup, "property", "og:site_name")
    if site_name:
        meta["site_name"] = site_name[:120]
    author = _meta_content(soup, "name", "author") or _meta_content(
        soup, "property", "article:author"
    )
    if author:
        meta["author"] = author[:120]
    og_type = _meta_content(soup, "property", "og:type")
    if og_type:
        meta["og_type"] = og_type[:40]
    section = _meta_content(soup, "property", "article:section")
    if section:
        meta["section"] = section[:80]
    tags = [
        str(t["content"]).strip()
        for t in soup.find_all("meta", attrs={"property": "article:tag"})
        if t.get("content")
    ]
    if tags:
        meta["tags"] = ",".join(tags[:10])[:300]
    return meta


def _parse_iso(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    # Drop a trailing timezone name in parentheses some CMSes emit.
    raw = re.sub(r"\s*\([^)]*\)\s*$", "", raw)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        if not m:
            return None
        try:
            dt = datetime.fromisoformat(m.group(1))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def parse_published_date(value: str) -> date | None:
    """Parse a metadata publish timestamp to a calendar date, or None."""
    dt = _parse_iso(value)
    return dt.date() if dt else None


def published_age_days(published_at: str) -> float | None:
    """Age of a page in days from its publish date; None if unparseable."""
    dt = _parse_iso(published_at)
    if dt is None:
        return None
    return (datetime.now(tz=UTC) - dt).total_seconds() / 86400.0


def is_stale_page(published_at: str, max_age_days: int) -> bool:
    """True only when we have a date AND it's older than the window. No date =>
    not stale (don't penalise pages that simply omit a publish date)."""
    age = published_age_days(published_at)
    return age is not None and age > max_age_days
