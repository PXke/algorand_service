"""Shared test doubles for fakes that were being hand-rolled per-file (Redis, Cassandra one-row results). Keep these minimal — a test whose fake needs bespoke execute()-branching logic should still define its own session class and only reach for FakeCassandraResult/FakeRedis as building blocks."""

from __future__ import annotations

import os
from typing import Any, Never
from unittest.mock import MagicMock

import pytest

# Tests must never report to Bugsnag: celery_app configures it at import time
# with a hardcoded default key (release_stage "prod") and attaches an ERROR-log
# handler to the root logger, so every error-path unit test shipped a prod
# event. An empty key makes _init_bugsnag return before configuring anything;
# set here so it is in place before any test imports celery_app.
os.environ["BUGSNAG_API_KEY"] = ""


def _install_no_network_guard() -> None:
    """Unit tests must never open real sockets — before this guard, tests were quietly dialing live Redis (:6379), Cassandra (:9042), and in one case the public internet. Any connect fails with ConnectionRefusedError naming the target — the same failure mode as "service not running", so code under test that deliberately exercises a backend-down path behaves as before, just without a real connection attempt. Installed process-wide at conftest import (not a per-test fixture) so driver background threads — e.g. the Cassandra reconnector — stay blocked between tests too. A test that trips this should fake the client at its seam (see fake_redis / fake_cassandra_session below)."""
    import errno
    import socket

    def _blocked_connect(_self: socket.socket, address: object) -> Never:
        raise ConnectionRefusedError(
            errno.ECONNREFUSED,
            f"unit test attempted a real network connection to {address}",
        )

    socket.socket.connect = _blocked_connect


_install_no_network_guard()


def _install_no_sleep_guard() -> None:
    """Unit tests must never really sleep -- the no-network guard above means every retry-with-backoff code path (LLM provider network/rate-limit retries, scrape retries, etc.) is guaranteed to fail on every attempt, so `time.sleep`ing through the real backoff schedule (up to 4 retries * up to 120s each in the LLM provider) burns real wall-clock time for zero information. Five test files were independently hand-rolling the identical `monkeypatch.setattr("time.sleep", ...)` fixture before this; installed process-wide (not per-test) so newly-added retry-heavy tests get it automatically instead of silently costing minutes until someone notices."""
    import time

    time.sleep = lambda _seconds=0, *_a, **_kw: None


_install_no_sleep_guard()


class FakeRedis:
    """In-memory stand-in for the redis-py client, covering the get/set/incr/decr/expire/exists surface used across the crawl-budget, cooldown, and publish-cap modules.

    Not a real TTL — expire() is a no-op that returns True, matching what
    those modules' tests actually assert on.
    """

    def __init__(self) -> None:
        """Start with an empty in-process key/value store."""
        self.store: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    def get(self, key: str) -> str | None:
        """Return the stored value for a key, or None if absent."""
        return self.store.get(key)

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:  # noqa: ARG002 -- name must match the real callee's keyword arg
        """Set a key's value, honoring nx (no-overwrite); ex (TTL) is accepted but not enforced."""
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        return True

    def incr(self, key: str) -> int:
        """Increment a key's integer value and return the new value."""
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    def decr(self, key: str) -> int:
        """Decrement a key's integer value (floored at 0) and return the new value."""
        current = max(0, int(self.store.get(key, "0")) - 1)
        self.store[key] = str(current)
        return current

    def expire(self, _key: str, _ttl: int) -> bool:
        """No-op TTL set; always returns True."""
        return True

    def exists(self, key: str) -> int:
        """Return 1 if the key exists, else 0."""
        return 1 if key in self.store else 0

    def sadd(self, key: str, *values: str) -> int:
        """Add members to a set key, returning the count actually added."""
        existing = self.sets.setdefault(key, set())
        before = len(existing)
        existing.update(values)
        return len(existing) - before

    def smembers(self, key: str) -> set[str]:
        """Return a set key's members (empty set if absent)."""
        return set(self.sets.get(key, set()))


@pytest.fixture
def fake_redis() -> FakeRedis:
    """Provide a fresh in-memory fake Redis client for a test."""
    return FakeRedis()


@pytest.fixture
def patch_redis_from_url(monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> FakeRedis:
    """For modules that build their client via ``redis.from_url(...)``."""
    import redis

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake_redis)
    return fake_redis


class FakeCassandraResult:
    """Wraps a single row (or None) the way a driver ResultSet's ``.one()`` does — the recurring shape every hand-rolled fake session was reimplementing."""

    def __init__(self, row: Any = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row/result
        """Wrap the given row (or None) for a later .one() call."""
        self._row = row

    def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
        """Return the wrapped row (or None)."""
        return self._row


@pytest.fixture
def fake_cassandra_session(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Auto-patches app.core.cassandra.get_cassandra_session to return a MagicMock, for tests that only need to assert on call args / stub .execute(...).one() rather than branch on the query text."""
    session = MagicMock()
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)
    return session


@pytest.fixture(autouse=True)
def _no_live_mistral_model_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any OpenAI-compatible provider's __init__ fetches live model metadata (context length, reasoning_effort support) from that provider's own GET /v1/models — never let that touch the network in tests (added 2026-07-15; every real construction anywhere in the suite would otherwise pay a real, uncontrolled network round-trip). Autouse so this protects every test, not just the ones that already know to mock it. Also clears the module-level cache each test so one test's metadata can't leak into another's assertions. A test that wants specific metadata can still monkeypatch _fetch_model_metadata itself afterward to override this.

    Patched at llm_openai_compatible.py (2026-08-15 rename: this is where
    OpenAICompatibleProvider.__init__ actually calls it from) -- also
    re-patched on mistral_client's backward-compat re-export of the same
    name, in case any test still patches that path directly.
    """
    import app.modules.ai.llm_openai_compatible as loc
    import app.modules.ai.mistral_client as mc

    monkeypatch.setattr(loc, "_fetch_model_metadata", lambda **_kw: {})
    monkeypatch.setattr(mc, "_fetch_model_metadata", lambda **_kw: {})
    loc._model_metadata_cache.clear()


@pytest.fixture(autouse=True)
def _no_peak_hours_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """article_composer's _require_off_peak() (2026-08-15) checks the real wall clock against config.LLM_PEAK_HOURS_UTC — every test that reaches compose_scrape_article/compose_weekly_digest without this would otherwise flakily pass or fail depending on what UTC hour the suite happens to run at. Autouse, default off (empty windows = fail-open = always off-peak) so the whole suite is time-independent by default; a test that specifically wants to exercise the peak-hours gate can monkeypatch config.LLM_PEAK_HOURS_UTC back to a real value itself afterward."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "")
