"""app.core.redis_client.get_redis(): process-cached client, one pool per (decode_responses, socket_connect_timeout) combination instead of a fresh redis.from_url() dial per call."""

from __future__ import annotations

import pytest

from app.core.redis_client import get_redis


def test_same_kwargs_return_the_same_cached_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two calls with identical kwargs must return the exact same object -- one dial, not one per call."""
    calls: list[dict] = []

    class _Client:
        pass

    def _fake_from_url(url: str, **kwargs: object) -> _Client:
        calls.append({"url": url, **kwargs})
        return _Client()

    import redis

    monkeypatch.setattr(redis, "from_url", _fake_from_url)

    first = get_redis()
    second = get_redis()

    assert first is second
    assert len(calls) == 1, "a second call with the same kwargs must not re-dial"


def test_distinct_kwargs_get_distinct_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """A binary client (decode_responses=False) and a decoded-str client are cached separately -- one must not shadow the other."""
    calls: list[dict] = []

    class _Client:
        def __init__(self, tag: object) -> None:
            self.tag = tag

    def _fake_from_url(url: str, **kwargs: object) -> _Client:
        calls.append({"url": url, **kwargs})
        return _Client(kwargs.get("decode_responses"))

    import redis

    monkeypatch.setattr(redis, "from_url", _fake_from_url)

    decoded = get_redis(decode_responses=True)
    binary = get_redis(decode_responses=False)

    assert decoded is not binary
    assert decoded.tag is True
    assert binary.tag is False
    assert len(calls) == 2


def test_socket_connect_timeout_only_passed_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """socket_connect_timeout is only forwarded to redis.from_url when a caller actually passes one, matching every call site's original kwargs."""
    seen: list[dict] = []

    class _Client:
        pass

    def _fake_from_url(_url: str, **kwargs: object) -> _Client:
        seen.append(kwargs)
        return _Client()

    import redis

    monkeypatch.setattr(redis, "from_url", _fake_from_url)

    get_redis()
    get_redis(socket_connect_timeout=2)

    assert "socket_connect_timeout" not in seen[0]
    assert seen[1]["socket_connect_timeout"] == 2


def test_uses_configured_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client is bound to app.core.config.REDIS_URL, not a hardcoded default."""
    seen: list[str] = []

    class _Client:
        pass

    def _fake_from_url(url: str, **_kwargs: object) -> _Client:
        seen.append(url)
        return _Client()

    import redis

    monkeypatch.setattr(redis, "from_url", _fake_from_url)
    monkeypatch.setattr("app.core.config.REDIS_URL", "redis://custom-host:6390/3")

    get_redis()

    assert seen == ["redis://custom-host:6390/3"]
