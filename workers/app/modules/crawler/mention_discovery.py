"""Mention-based discovery: turn "someone wrote about it" into frontier entries.

Link-following only finds what already-crawled pages link to, and the curated
sync lanes only find what curators list. This lane watches where new Algorand
projects get *mentioned* first — GitHub repos tagged `algorand` (the repo's
`homepage` field is the project's own site) and the Medium `algorand` tag feed
(post bodies link the products they cover) — and enqueues the linked domains
for a normal crawl. Discovered URLs enter the standard frontier funnel
(unknown domains held pending, preview-scored, auto-approve rules apply);
a mention is a lead, not a curated listing, so no relevance anchor is granted.

Both sources are public and unauthenticated.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from app.modules.crawler.ecosystem_sync import _skippable

logger = logging.getLogger(__name__)

_HREF_RE = re.compile(r'href="(https?://[^"#]+)"')


def _clean_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def homepages_from_github_topic(payload: dict[str, Any]) -> dict[str, str]:
    """Url -> attribution from a GitHub repo-search response: each repo's self-declared `homepage` (the project's own site)."""
    out: dict[str, str] = {}
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        homepage = str(item.get("homepage") or "").strip()
        if not homepage.lower().startswith(("http://", "https://")):
            continue
        host = _clean_host(homepage)
        if not host or "." not in host or _skippable(host):
            continue
        out.setdefault(homepage, f"github:{item.get('full_name', '')}")
    return out


def urls_from_feed_html(xml_text: str, *, self_host: str) -> dict[str, str]:
    """Url -> attribution for external links inside a tag feed's post bodies (self-links and skip-listed hosts dropped, one URL per external domain). Feed bodies ship as entity-escaped HTML inside CDATA, so unescape first."""
    import html

    out: dict[str, str] = {}
    seen_hosts: set[str] = set()
    for url in _HREF_RE.findall(html.unescape(xml_text or "")):
        host = _clean_host(url)
        if (
            not host
            or "." not in host
            or host == self_host
            or host.endswith(f".{self_host}")
            or _skippable(host)
            or host in seen_hosts
        ):
            continue
        seen_hosts.add(host)
        out[url] = f"feed:{self_host}"
    return out


def discover_from_mentions() -> dict[str, Any]:
    """Fetch each mention source and enqueue discovered URLs for crawling."""
    from app.core import config
    from app.core.net_guard import guarded_get
    from app.modules.crawler.url_queue import enqueue_url

    stats = {"sources": 0, "urls": 0, "enqueued": 0, "errors": 0}

    def _enqueue(candidates: dict[str, str], source: str) -> None:
        for url in sorted(candidates):
            stats["urls"] += 1
            try:
                _, created = enqueue_url(url, source=source, priority=30)
                if created:
                    stats["enqueued"] += 1
            except Exception:
                logger.warning("mention discovery: enqueue failed for %s", url, exc_info=True)
                stats["errors"] += 1

    try:
        resp = guarded_get(
            "https://api.github.com/search/repositories",
            params={
                "q": "topic:algorand",
                "sort": "updated",
                "per_page": str(config.MENTION_GITHUB_REPO_CAP),
            },
            timeout=20.0,
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        _enqueue(homepages_from_github_topic(resp.json()), "github-topic")
        stats["sources"] += 1
    except Exception:
        logger.warning("mention discovery: github topic fetch failed", exc_info=True)
        stats["errors"] += 1

    try:
        resp = guarded_get("https://medium.com/feed/tag/algorand", timeout=20.0)
        resp.raise_for_status()
        _enqueue(urls_from_feed_html(resp.text, self_host="medium.com"), "medium-tag")
        stats["sources"] += 1
    except Exception:
        logger.warning("mention discovery: medium tag fetch failed", exc_info=True)
        stats["errors"] += 1

    return stats
