"""IndexNow: push new article URLs to participating search engines on publish.

One POST to api.indexnow.org fans out to Bing (which powers Ecosia, DuckDuckGo
and Yahoo), Yandex, Seznam and Naver — so freshly published stories get picked
up in minutes instead of waiting for a crawl. Best-effort: never block or fail a
publish over this. The key is public (also served as {key}.txt at the site root).
"""

from __future__ import annotations

import logging

import httpx

from app.core import config

log = logging.getLogger(__name__)

_ENDPOINT = "https://api.indexnow.org/indexnow"


def article_url(article_id: str) -> str:
    return f"{config.PUBLIC_SITE_URL}/news/articles/{article_id}"


def ping(urls: list[str]) -> None:
    """Notify IndexNow of new/updated URLs. No-op without a key or URLs."""
    key = (config.INDEXNOW_KEY or "").strip()
    urls = [u for u in urls if u]
    if not key or not urls:
        return
    host = config.PUBLIC_SITE_URL.split("://", 1)[-1].split("/", 1)[0]
    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{config.PUBLIC_SITE_URL}/{key}.txt",
        "urlList": urls,
    }
    try:
        resp = httpx.post(_ENDPOINT, json=payload, timeout=8.0)
        log.info("indexnow ping %s -> %s", urls, resp.status_code)
    except Exception as exc:  # network/timeout — best-effort only
        log.info("indexnow ping failed (%s): %s", urls, exc)
