"""Shared, lazily-built Redis client.

Every module that needs Redis had its own `@lru_cache` + `redis.from_url`
copy (contact, sharing, media, seo, news view counts, admin). CLAUDE.md
section 3 asks new code to reach for a shared cached client instead of adding
another one, so this is that accessor.

Construction stays lazy and `redis.from_url` does not dial until the first
command, so importing this module never opens a connection — the API and its
tests must boot without a reachable Redis.

Existing callers are deliberately NOT migrated here; this is the accessor new
code uses, not a refactor of the ones that already work.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    import redis


@lru_cache(maxsize=4)
def get_redis(*, decode_responses: bool = True) -> redis.Redis:
    """Return the process-wide Redis client for the given decoding mode.

    Cached per `decode_responses` value: bytes-returning callers (media) and
    str-returning callers (rate limits, caches) need different clients, and
    sharing one would hand the wrong type to whichever asked second.
    """
    import redis

    return redis.from_url(
        settings.redis_url,
        decode_responses=decode_responses,
        socket_connect_timeout=2,
    )
