"""Best-effort distributed locks (Redis) so the same unit of work is not run by multiple Celery workers at the same time.

Why: with --concurrency=4, beat tasks and re-dispatches can hand the SAME source
or queue row to several workers at once, so all four scrape/crawl/compose the
exact same thing in parallel (4x the work, and external sites hit 4x). A claim on
a per-work-unit key lets the first worker win and the rest skip.

Best-effort by design: if Redis is unreachable, acquire() returns a token (truthy)
so real work is never blocked by a lock outage.

Token-checked release: each acquire mints a random token stored as the key's
value, and release only deletes the key if the value still matches that token.
This stops a worker whose lock already EXPIRED (compose outran the TTL) from
deleting the lock a *different* worker has since acquired. It is NOT a full
fencing protocol (no Redlock, single-node), just the cheap correctness win.
"""

from __future__ import annotations

import contextlib
import functools
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.core.redis_client import get_redis

if TYPE_CHECKING:
    import redis

# Delete the key only if it still holds the token we wrote, so we never release
# someone else's lock after ours expired underneath us.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


def _client() -> redis.Redis:
    return get_redis(decode_responses=False)


def acquire(key: str, ttl: int) -> str | None:
    """Claim `key` for `ttl` seconds.

    Returns an opaque token to pass back to `release()` if acquired (or if Redis
    is down — fail-open). Returns None if the lock is already held by someone else.
    """
    token = secrets.token_hex(16)
    try:
        if _client().set(f"lock:{key}", token, nx=True, ex=ttl):
            return token
        return None
    except Exception:
        # Redis unreachable: never block real work. The returned token will not
        # match anything on release (the key was never written), so release is a
        # harmless no-op.
        return token


def release(key: str, token: str) -> None:
    """Release `key`, but only if it still holds our `token`."""
    if not token:
        return
    with contextlib.suppress(Exception):
        _client().eval(_RELEASE_LUA, 1, f"lock:{key}", token)


def single_flight(
    key_fn: Callable[..., str], *, ttl: int
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Decorator: run at most one instance of the wrapped function for a given key across all workers. A concurrent call with the same key returns ``{"status": "already_running", "key": ...}`` without executing the body."""

    def deco(fn: Callable[..., object]) -> Callable[..., object]:
        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> object:
            key = key_fn(*args, **kwargs)
            token = acquire(key, ttl)
            if token is None:
                return {"status": "already_running", "key": key}
            try:
                return fn(*args, **kwargs)
            finally:
                release(key, token)

        return wrapper

    return deco
