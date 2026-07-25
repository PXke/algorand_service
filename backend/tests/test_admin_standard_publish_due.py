"""Standard-publish pacing must fail closed on a Redis error."""

from __future__ import annotations

from typing import Never

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore


class _BoomRedis:
    def get(self, _key: str) -> Never:
        raise ConnectionError("redis down")


def test_is_standard_publish_due_fails_closed_on_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis outage must never look like 'clock elapsed, publish now' — that would silently bypass the pacing cadence (matches the workers' publish_schedule.is_standard_publish_due, which fails the same way). Previously this returned True unconditionally on any exception, bypassing the standard-publish cadence entirely on a Redis blip."""
    import redis

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_kw: _BoomRedis())

    store = AdminCassandraStore()
    assert store._is_standard_publish_due() is False


def test_is_standard_publish_due_true_when_never_published(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treats a never-published site (no cached timestamp) as due for a standard publish."""
    class _EmptyRedis:
        def get(self, _key: str) -> None:
            return None

    import redis

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_kw: _EmptyRedis())

    store = AdminCassandraStore()
    assert store._is_standard_publish_due() is True
