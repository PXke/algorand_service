"""Shared test doubles for fakes that were being hand-rolled per-file (Redis,
Cassandra one-row results). Keep these minimal — a test whose fake needs
bespoke execute()-branching logic should still define its own session class
and only reach for FakeCassandraResult/FakeRedis as building blocks."""

from __future__ import annotations

import pytest


class FakeRedis:
    """In-memory stand-in for the redis-py client, covering the get/set/incr/
    decr/expire/exists surface used across the crawl-budget, cooldown, and
    publish-cap modules. Not a real TTL — expire() is a no-op that returns
    True, matching what those modules' tests actually assert on."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, nx: bool = False, ex: int | None = None):
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        return True

    def incr(self, key):
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    def decr(self, key):
        current = max(0, int(self.store.get(key, "0")) - 1)
        self.store[key] = str(current)
        return current

    def expire(self, key, ttl):
        return True

    def exists(self, key):
        return 1 if key in self.store else 0


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def patch_redis_from_url(monkeypatch, fake_redis: FakeRedis) -> FakeRedis:
    """For modules that build their client via ``redis.from_url(...)``."""
    import redis

    monkeypatch.setattr(redis, "from_url", lambda *a, **k: fake_redis)
    return fake_redis


class FakeCassandraResult:
    """Wraps a single row (or None) the way a driver ResultSet's ``.one()``
    does — the recurring shape every hand-rolled fake session was
    reimplementing."""

    def __init__(self, row=None) -> None:
        self._row = row

    def one(self):
        return self._row


@pytest.fixture
def fake_cassandra_session(monkeypatch):
    """Auto-patches app.core.cassandra.get_cassandra_session to return a
    MagicMock, for tests that only need to assert on call args / stub
    .execute(...).one() rather than branch on the query text."""
    from unittest.mock import MagicMock

    session = MagicMock()
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)
    return session
