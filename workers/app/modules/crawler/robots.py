"""robots.txt politeness for the frontier crawler.

We fetch a host's robots.txt once, cache the raw text in Redis (24h), and check
each candidate URL against it for our crawler user-agent before fetching. Fails
OPEN: any error (no robots.txt, fetch failure, parse error) means "allowed", so a
robots problem never silently halts the whole crawl — it just removes the guard.
"""

from __future__ import annotations

import contextlib
import urllib.robotparser
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from app.core.config import CRAWLER_RESPECT_ROBOTS, CRAWLER_USER_AGENT
from app.core.redis_client import get_redis

if TYPE_CHECKING:
    import redis

_CACHE_TTL = 86_400  # 24h
_MISSING = "__NONE__"  # sentinel: host has no usable robots.txt (cache the absence)


def _client() -> redis.Redis:
    return get_redis()


def _key(netloc: str) -> str:
    return f"robots:{netloc}"


def _robots_text(scheme: str, netloc: str) -> str | None:
    """Cached raw robots.txt for a host, or None when it has none."""
    key = _key(netloc)
    try:
        cached = _client().get(key)
    except Exception:
        cached = None
    if cached is not None:
        return None if cached == _MISSING else cached

    text: str | None = None
    try:
        from app.core.net_guard import guarded_get

        resp = guarded_get(
            f"{scheme}://{netloc}/robots.txt",
            timeout=10.0,
            headers={"User-Agent": CRAWLER_USER_AGENT},
        )
        if resp.status_code == 200 and resp.text.strip():
            text = resp.text[:200_000]
    except Exception:
        text = None

    with contextlib.suppress(Exception):
        _client().set(key, text if text is not None else _MISSING, ex=_CACHE_TTL)
    return text


def is_allowed(url: str) -> bool:
    """Whether our crawler may fetch ``url`` per the host's robots.txt."""
    if not CRAWLER_RESPECT_ROBOTS:
        return True
    try:
        parsed = urlparse(url)
    except Exception:
        return True
    if not parsed.scheme or not parsed.netloc:
        return True

    text = _robots_text(parsed.scheme, parsed.netloc)
    if not text:
        return True  # no robots.txt → unrestricted

    parser = urllib.robotparser.RobotFileParser()
    try:
        parser.parse(text.splitlines())
        return parser.can_fetch(CRAWLER_USER_AGENT, url)
    except Exception:
        return True
