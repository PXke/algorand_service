"""Shared test doubles for fakes that were being hand-rolled per-file (Redis,
Cassandra one-row results). Keep these minimal — a test whose fake needs
bespoke execute()-branching logic should still define its own session class
and only reach for FakeCassandraResult/FakeRedis as building blocks."""

from __future__ import annotations

import os

import pytest

# Tests must never report to Bugsnag: celery_app configures it at import time
# with a hardcoded default key (release_stage "prod") and attaches an ERROR-log
# handler to the root logger, so every error-path unit test shipped a prod
# event. An empty key makes _init_bugsnag return before configuring anything;
# set here so it is in place before any test imports celery_app.
os.environ["BUGSNAG_API_KEY"] = ""


def _install_no_network_guard() -> None:
    """Unit tests must never open real sockets — before this guard, tests were
    quietly dialing live Redis (:6379), Cassandra (:9042), and in one case the
    public internet. Any connect fails with ConnectionRefusedError naming the
    target — the same failure mode as "service not running", so code under test
    that deliberately exercises a backend-down path behaves as before, just
    without a real connection attempt. Installed process-wide at conftest
    import (not a per-test fixture) so driver background threads — e.g. the
    Cassandra reconnector — stay blocked between tests too. A test that trips
    this should fake the client at its seam (see fake_redis /
    fake_cassandra_session below)."""
    import errno
    import socket

    def _blocked_connect(self, address):
        raise ConnectionRefusedError(
            errno.ECONNREFUSED,
            f"unit test attempted a real network connection to {address}",
        )

    socket.socket.connect = _blocked_connect


_install_no_network_guard()


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


@pytest.fixture(autouse=True)
def _no_live_mistral_model_metadata(monkeypatch):
    """MistralClient.__init__ fetches live model metadata (context length,
    reasoning_effort support) from Mistral's own GET /v1/models — never let
    that touch the network in tests (added 2026-07-15; every real
    MistralClient() construction anywhere in the suite would otherwise pay a
    real, uncontrolled network round-trip). Autouse so this protects every
    test, not just the ones that already know to mock it. Also clears the
    module-level cache each test so one test's metadata can't leak into
    another's assertions. A test that wants specific metadata can still
    monkeypatch _fetch_model_metadata itself afterward to override this."""
    import app.modules.ai.mistral_client as mc

    monkeypatch.setattr(mc, "_fetch_model_metadata", lambda **kw: {})
    mc._model_metadata_cache.clear()
