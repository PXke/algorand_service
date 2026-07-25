"""Fetch context for social posts (e.g. tweets) linked from a page."""

from __future__ import annotations

import html
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

_TWEET_URL = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]+/status/\d+",
    re.I,
)

_OEMBED = "https://publish.twitter.com/oembed"


def extract_post_urls(text: str) -> list[str]:
    """Pull up to 5 distinct X/Twitter status URLs out of a page's text."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in _TWEET_URL.finditer(text):
        url = match.group(0).split("?")[0]
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls[:5]


def _html_to_plain(fragment: str) -> str:
    soup = BeautifulSoup(fragment, "html.parser")
    return soup.get_text("\n", strip=True)


def fetch_tweet_context(tweet_url: str, *, timeout: float = 15.0) -> dict[str, Any]:
    """Public tweet metadata via Twitter oEmbed (no API key).

    Use only for URLs already present in trusted ingest (Discord mirror, push).
    """
    result: dict[str, Any] = {"url": tweet_url}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(_OEMBED, params={"url": tweet_url, "omit_script": "true"})
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        result["error"] = str(exc)[:200]
        return result

    author = str(data.get("author_name", "")).strip()
    author_url = str(data.get("author_url", "")).strip()
    raw_html = str(data.get("html", ""))
    text = _html_to_plain(raw_html) if raw_html else ""
    result.update(
        {
            "author": author,
            "author_url": author_url,
            "text": html.unescape(text)[:2000],
            "provider": "twitter_oembed",
        }
    )
    return result


def enrich_linked_posts(page_text: str, *, enabled: bool = True) -> dict[str, Any]:
    """Fetch oEmbed context for every tweet URL linked in the page text, if enabled."""
    urls = extract_post_urls(page_text)
    if not urls:
        return {"linked_posts": [], "count": 0}

    if not enabled:
        return {
            "linked_posts": [{"url": u, "status": "fetch_disabled"} for u in urls],
            "count": len(urls),
        }

    posts: list[dict[str, Any]] = [fetch_tweet_context(url) for url in urls]
    return {"linked_posts": posts, "count": len(posts)}
