"""IndexNow pings for the admin paths that change a public URL.

Mirror of the workers' newspaper/indexnow.py (the two apps don't share code):
one POST to api.indexnow.org fans out to Bing (which powers Copilot grounding,
Ecosia, DuckDuckGo, Yahoo), Yandex, Seznam and Naver. Bing's guidelines ask for
a notification on ADD, UPDATE and REMOVE — the workers cover auto-publish and
edits; this covers admin approve-to-feed, patch, and delete (a submitted
deleted URL gets recrawled, sees the 410 tombstone, and drops out).
Best-effort: never block or fail an admin action over this.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

_ENDPOINT = "https://api.indexnow.org/indexnow"


def article_url(article_id: str) -> str:
    return f"{settings.public_site_url}/news/articles/{article_id}"


def ping(urls: list[str]) -> None:
    """Notify IndexNow of added/updated/removed URLs. No-op without key/URLs."""
    key = (settings.indexnow_key or "").strip()
    urls = [u for u in urls if u]
    if not key or not urls:
        return
    host = settings.public_site_url.split("://", 1)[-1].split("/", 1)[0]
    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{settings.public_site_url}/{key}.txt",
        "urlList": urls,
    }
    try:
        resp = httpx.post(_ENDPOINT, json=payload, timeout=8.0)
        log.info("indexnow ping %s -> %s", urls, resp.status_code)
    except Exception as exc:  # network/timeout — best-effort only
        log.info("indexnow ping failed (%s): %s", urls, exc)
