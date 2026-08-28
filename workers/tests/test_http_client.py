"""app.core.http_client.get_http_client(): process-cached httpx.Client, mirroring test_redis_client.py's coverage of get_redis() for the same functools.cache pattern.

Constructing a real httpx.Client() never opens a socket (httpcore's
connection pool is built lazily, on first request) -- these tests are safe
under the suite's no-network guard as long as they never actually call
.get()/.post() on the returned client.
"""

from __future__ import annotations

import httpx

from app.core.http_client import get_http_client


def test_returns_an_httpx_client() -> None:
    """get_http_client() returns a real httpx.Client instance."""
    client = get_http_client()
    assert isinstance(client, httpx.Client)


def test_same_kwargs_return_the_same_cached_instance() -> None:
    """Two calls with identical (timeout, follow_redirects, base_url) share one client -- the whole point: no fresh TCP/TLS handshake per call."""
    a = get_http_client(timeout=12.0, follow_redirects=False)
    b = get_http_client(timeout=12.0, follow_redirects=False)
    assert a is b


def test_different_timeout_gets_its_own_client() -> None:
    """A distinct timeout is a distinct cache key -- callers needing different timeouts don't clobber each other's client."""
    a = get_http_client(timeout=12.0)
    b = get_http_client(timeout=20.0)
    assert a is not b


def test_different_follow_redirects_gets_its_own_client() -> None:
    """follow_redirects is part of the cache key too -- net_guard.guarded_get (follow_redirects=False, manual revalidation) must never share a pool with a caller that wants real redirect-following."""
    a = get_http_client(timeout=12.0, follow_redirects=False)
    b = get_http_client(timeout=12.0, follow_redirects=True)
    assert a is not b


def test_different_base_url_gets_its_own_client() -> None:
    """A fixed base_url (e.g. bluesky.py's _SERVICE) is part of the cache key -- distinct from the base_url="" default every other caller uses."""
    a = get_http_client(timeout=15.0)
    b = get_http_client(timeout=15.0, base_url="https://bsky.social")
    assert a is not b


def test_cache_clear_forces_a_fresh_client() -> None:
    """Clearing the cache (what celery_app's worker_process_init hook does on every forked child) makes the next call build a brand new client rather than reusing whatever the parent process had cached."""
    first = get_http_client(timeout=12.0)
    get_http_client.cache_clear()
    second = get_http_client(timeout=12.0)
    assert first is not second
