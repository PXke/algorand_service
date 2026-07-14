from __future__ import annotations

from app.modules.admin.stores.cassandra import AdminCassandraStore


class _BoomRedis:
    def get(self, key):
        raise ConnectionError("redis down")


def test_is_standard_publish_due_fails_closed_on_redis_error(monkeypatch) -> None:
    """A Redis outage must never look like 'clock elapsed, publish now' — that
    would silently bypass the pacing cadence (matches the workers'
    publish_schedule.is_standard_publish_due, which fails the same way).
    Previously this returned True unconditionally on any exception, bypassing
    the standard-publish cadence entirely on a Redis blip."""
    import redis

    monkeypatch.setattr(redis, "from_url", lambda *a, **kw: _BoomRedis())

    store = AdminCassandraStore()
    assert store._is_standard_publish_due() is False


def test_is_standard_publish_due_true_when_never_published(monkeypatch) -> None:
    class _EmptyRedis:
        def get(self, key):
            return None

    import redis

    monkeypatch.setattr(redis, "from_url", lambda *a, **kw: _EmptyRedis())

    store = AdminCassandraStore()
    assert store._is_standard_publish_due() is True
