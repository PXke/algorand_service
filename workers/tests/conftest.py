"""Shared test doubles for fakes that were being hand-rolled per-file (Redis, Cassandra one-row results). Keep these minimal — a test whose fake needs bespoke execute()-branching logic should still define its own session class and only reach for FakeCassandraResult/FakeRedis as building blocks."""

from __future__ import annotations

import os
from types import SimpleNamespace
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

    Patched at llm_openai_compatible.py -- this is where
    OpenAICompatibleProvider.__init__ actually calls it from (the
    mistral_client.py backward-compat shim that used to re-export this name
    was deleted 2026-08-25, its ~29 importers moved to the real homes).
    """
    import app.modules.ai.llm_openai_compatible as loc

    monkeypatch.setattr(loc, "_fetch_model_metadata", lambda **_kw: {})
    loc._model_metadata_cache.clear()


def _artifact_cql(cls: type, name: str) -> str:
    """Read a `_Stmt`'s raw CQL text via the class `__dict__` (bypasses the descriptor's `__get__`, which calls `prepare_cached` and needs a live session)."""
    return cls.__dict__[name].cql


class _ArtifactRows(list):
    """A list of rows that ALSO supports `.one()` -- artifact_store.py sometimes chains `.one()` on a point query and sometimes iterates a multi-row result directly over the same `session.execute(...)` return value."""

    def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row
        """Return the first row, or None when empty."""
        return self[0] if self else None


def _artifact_rows(rows: list) -> _ArtifactRows:
    return _ArtifactRows(rows)


class FakeArtifactSession:
    """In-memory artifacts/artifacts_pending/artifact_content/to_compose tables (2026-08-25 editorial-room shadow schema), keyed by the exact CQL text of `app.core.statements.ArtifactStmts`/`ToComposeStmts` so it exercises the real store/priority/selection code against its real prepared-statement call sites, not just a call-capturing mock. Shared across test_artifact_store.py, test_artifact_priority.py, and test_to_compose_selection.py via the `fake_artifact_session` fixture below — genuinely identical needs across all three, unlike the bespoke-per-file fakes elsewhere in this suite."""

    def __init__(self) -> None:
        """Start with all four tables empty and the CQL-text dispatch table wired."""
        from app.core.statements import ArtifactStmts, ToComposeStmts

        self.artifacts: dict[str, dict] = {}
        self.pending: dict[tuple, dict] = {}
        self.content: dict[str, dict] = {}
        self.to_compose: dict[tuple, dict] = {}
        self._handlers = {
            _artifact_cql(ArtifactStmts, "INSERT"): self._insert_artifact,
            _artifact_cql(ArtifactStmts, "INSERT_PENDING"): self._insert_pending,
            _artifact_cql(ArtifactStmts, "DELETE_PENDING"): self._delete_pending,
            _artifact_cql(ArtifactStmts, "LIST_PENDING"): self._list_pending,
            _artifact_cql(ArtifactStmts, "SET_PENDING_HUMAN_PICK"): self._set_pending_human_pick,
            _artifact_cql(ArtifactStmts, "SET_VENUE_SERVICE_ID"): self._set_venue_service_id,
            _artifact_cql(
                ArtifactStmts, "SET_PENDING_VENUE_SERVICE_ID"
            ): self._set_pending_venue_service_id,
            _artifact_cql(ArtifactStmts, "GET"): self._get_artifact,
            _artifact_cql(ArtifactStmts, "GET_STATUS_ROW"): self._get_status_row,
            _artifact_cql(ArtifactStmts, "UPDATE_STATUS"): self._update_status,
            _artifact_cql(ArtifactStmts, "UPDATE_PRIORITY"): self._update_priority,
            _artifact_cql(ArtifactStmts, "SET_HUMAN_PICK"): self._set_human_pick,
            _artifact_cql(ArtifactStmts, "CLEAR_HUMAN_PICK"): self._clear_human_pick,
            _artifact_cql(ArtifactStmts, "INSERT_CONTENT"): self._insert_content,
            _artifact_cql(ArtifactStmts, "GET_CONTENT"): self._get_content,
            _artifact_cql(ToComposeStmts, "INSERT"): self._insert_to_compose,
            _artifact_cql(ToComposeStmts, "LIST_FOR_DAY"): self._list_to_compose,
            _artifact_cql(ToComposeStmts, "DELETE_FOR_DAY"): self._delete_to_compose_day,
        }

    def prepare(self, cql: str) -> str:
        """Identity prepare -- lets `execute()` dispatch on the raw CQL text itself."""
        return cql

    def execute(self, cql: str, params: tuple = ()) -> Any:  # noqa: ANN401 -- duck-typed Cassandra ResultSet
        """Dispatch to the in-memory handler matching this exact CQL string."""
        handler = self._handlers.get(cql)
        if handler is None:
            raise AssertionError(f"FakeArtifactSession: no handler wired for CQL: {cql!r}")
        return handler(tuple(params))

    # -- artifacts -----------------------------------------------------
    def _insert_artifact(self, p: tuple) -> None:
        (
            artifact_id,
            service_id,
            venue_service_id,
            url,
            channel,
            created_at,
            event_date,
            priority,
            priority_computed_at,
            status,
            human_pick_day,
        ) = p
        self.artifacts[str(artifact_id)] = {
            "artifact_id": artifact_id,
            "service_id": service_id,
            "venue_service_id": venue_service_id,
            "url": url,
            "channel": channel,
            "created_at": created_at,
            "event_date": event_date,
            "priority": priority,
            "priority_computed_at": priority_computed_at,
            "status": status,
            "human_pick_day": human_pick_day,
        }

    def _get_artifact(self, p: tuple) -> _ArtifactRows:
        (artifact_id,) = p
        row = self.artifacts.get(str(artifact_id))
        return _artifact_rows([SimpleNamespace(**row)] if row else [])

    def _get_status_row(self, p: tuple) -> _ArtifactRows:
        (artifact_id,) = p
        row = self.artifacts.get(str(artifact_id))
        if not row:
            return _artifact_rows([])
        return _artifact_rows(
            [SimpleNamespace(status=row["status"], priority=row["priority"], created_at=row["created_at"])]
        )

    def _update_status(self, p: tuple) -> None:
        status, artifact_id = p
        row = self.artifacts.get(str(artifact_id))
        if row:
            row["status"] = status

    def _update_priority(self, p: tuple) -> None:
        priority, computed_at, artifact_id = p
        row = self.artifacts.get(str(artifact_id))
        if row:
            row["priority"] = priority
            row["priority_computed_at"] = computed_at

    def _set_human_pick(self, p: tuple) -> None:
        day, artifact_id = p
        row = self.artifacts.get(str(artifact_id))
        if row:
            row["human_pick_day"] = day

    def _clear_human_pick(self, p: tuple) -> None:
        (artifact_id,) = p
        row = self.artifacts.get(str(artifact_id))
        if row:
            row["human_pick_day"] = None

    def _set_venue_service_id(self, p: tuple) -> None:
        venue_service_id, artifact_id = p
        row = self.artifacts.get(str(artifact_id))
        if row:
            row["venue_service_id"] = venue_service_id

    # -- artifacts_pending ----------------------------------------------
    @staticmethod
    def _pending_key(status: str, priority: float, created_at: object, artifact_id: object) -> tuple:
        return (status, priority, created_at, str(artifact_id))

    def _insert_pending(self, p: tuple) -> None:
        (
            status,
            priority,
            created_at,
            artifact_id,
            service_id,
            venue_service_id,
            channel,
            url,
            event_date,
            human_pick_day,
        ) = p
        key = self._pending_key(status, priority, created_at, artifact_id)
        self.pending[key] = {
            "status": status,
            "priority": priority,
            "created_at": created_at,
            "artifact_id": artifact_id,
            "service_id": service_id,
            "venue_service_id": venue_service_id,
            "channel": channel,
            "url": url,
            "event_date": event_date,
            "human_pick_day": human_pick_day,
        }

    def _delete_pending(self, p: tuple) -> None:
        status, priority, created_at, artifact_id = p
        self.pending.pop(self._pending_key(status, priority, created_at, artifact_id), None)

    def _list_pending(self, p: tuple) -> _ArtifactRows:
        status, limit = p
        rows = [r for r in self.pending.values() if r["status"] == status]
        rows.sort(key=lambda r: (-r["priority"], r["created_at"]))
        return _artifact_rows([SimpleNamespace(**r) for r in rows[:limit]])

    def _set_pending_human_pick(self, p: tuple) -> None:
        human_pick_day, status, priority, created_at, artifact_id = p
        row = self.pending.get(self._pending_key(status, priority, created_at, artifact_id))
        if row:
            row["human_pick_day"] = human_pick_day

    def _set_pending_venue_service_id(self, p: tuple) -> None:
        venue_service_id, status, priority, created_at, artifact_id = p
        row = self.pending.get(self._pending_key(status, priority, created_at, artifact_id))
        if row:
            row["venue_service_id"] = venue_service_id

    # -- artifact_content -------------------------------------------------
    def _insert_content(self, p: tuple) -> None:
        artifact_id, title, content, metadata = p
        self.content[str(artifact_id)] = {
            "artifact_id": artifact_id,
            "title": title,
            "content": content,
            "metadata": metadata,
        }

    def _get_content(self, p: tuple) -> _ArtifactRows:
        (artifact_id,) = p
        row = self.content.get(str(artifact_id))
        return _artifact_rows([SimpleNamespace(**row)] if row else [])

    # -- to_compose -------------------------------------------------------
    def _insert_to_compose(self, p: tuple) -> None:
        compose_day, slot, artifact_id, lane, service_id, picked_at = p
        self.to_compose[(compose_day, slot)] = {
            "compose_day": compose_day,
            "slot": slot,
            "artifact_id": artifact_id,
            "lane": lane,
            "service_id": service_id,
            "picked_at": picked_at,
        }

    def _list_to_compose(self, p: tuple) -> _ArtifactRows:
        (compose_day,) = p
        rows = [r for r in self.to_compose.values() if r["compose_day"] == compose_day]
        return _artifact_rows([SimpleNamespace(**r) for r in rows])

    def _delete_to_compose_day(self, p: tuple) -> None:
        (compose_day,) = p
        for key in [k for k in self.to_compose if k[0] == compose_day]:
            del self.to_compose[key]


@pytest.fixture
def fake_artifact_session(monkeypatch: pytest.MonkeyPatch) -> FakeArtifactSession:
    """Install a fresh FakeArtifactSession and clear the process-wide prepared-statement cache (prepare_cached caches by CQL string across the whole test process, so a stale prepared statement from an earlier real-session test must not leak in)."""
    import app.core.cassandra as c

    session = FakeArtifactSession()
    monkeypatch.setattr(c, "get_cassandra_session", lambda: session)
    c.prepare_cached.cache_clear()
    return session


@pytest.fixture(autouse=True)
def _no_peak_hours_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """article_composer's _require_off_peak() (2026-08-15) checks the real wall clock against config.LLM_PEAK_HOURS_UTC — every test that reaches compose_scrape_article/compose_weekly_digest without this would otherwise flakily pass or fail depending on what UTC hour the suite happens to run at. Autouse, default off (empty windows = fail-open = always off-peak) so the whole suite is time-independent by default; a test that specifically wants to exercise the peak-hours gate can monkeypatch config.LLM_PEAK_HOURS_UTC back to a real value itself afterward."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "")
