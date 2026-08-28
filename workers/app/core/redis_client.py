"""Process-cached Redis client for workers/.

Before this module, nearly every workers/ Redis touch point (cooldowns,
locks, caches, rate limiting) built its own client with a private
``_client()``/``_redis_client()`` helper that called
``redis.from_url(REDIS_URL, ...)`` fresh on every invocation -- one new
client object per call, not per process. ``redis.from_url`` itself is lazy
(it does not open a TCP connection until the first command runs, and the
underlying ``ConnectionPool`` reconnects on its own if a connection drops),
so constructing a fresh one is safe but wasteful: it re-parses the URL and
throws away a perfectly reusable connection pool every time.

``get_redis()`` caches one client per distinct (decode_responses,
socket_connect_timeout) combination for the life of the process, the same
one-client-per-process idea backend/app/core/cache.py's ``_client()``
already uses via ``@lru_cache(maxsize=1)`` -- this just gives workers/ a
single shared entry point instead of every module hand-rolling its own.

Existing per-module ``_client()``/``_redis_client()`` helpers now delegate
here; callers and tests that already monkeypatch those local wrappers (or
``redis.from_url`` itself) are unaffected. Tests that exercise this module
directly must clear the cache between cases -- see
``workers/tests/conftest.py``'s ``_clear_redis_client_cache`` autouse
fixture, which does this for the whole suite automatically.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis


@cache
def get_redis(
    *, decode_responses: bool = True, socket_connect_timeout: float | None = None
) -> redis.Redis:
    """Return a process-cached Redis client bound to ``REDIS_URL``.

    Cached per (decode_responses, socket_connect_timeout) pair, so a caller
    needing a binary client and one needing a decoded-str client each get
    their own pooled connection, reused across calls instead of dialing a
    new one every time.
    """
    import redis

    from app.core.config import REDIS_URL

    kwargs: dict[str, object] = {"decode_responses": decode_responses}
    if socket_connect_timeout is not None:
        kwargs["socket_connect_timeout"] = socket_connect_timeout
    return redis.from_url(REDIS_URL, **kwargs)
