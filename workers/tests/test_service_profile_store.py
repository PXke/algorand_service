"""get_stored_scale_signal must hand back a tz-aware datetime, never a naive one.

Root-caused 2026-09-01: the Cassandra driver returns a naive (UTC-valued)
datetime for a `timestamp` column. ingest_signal._resolve_stale_scale_signal
subtracts that from datetime.now(tz=UTC), which raises TypeError on a
naive-vs-aware subtraction -- this crashed every poll_forum_topics run that
hit a service with a previously-stored scale signal, every ~30 minutes since
at least 2026-08-31, until service_profile_store normalized the value here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import app.core.cassandra as cassandra_core
from app.modules.newspaper import service_profile_store


class _Result:
    def __init__(self, row: Any = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row/result
        self._row = row

    def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
        return self._row


class _FakeSession:
    def __init__(self, row: Any) -> None:  # noqa: ANN401 -- duck-typed Cassandra row/result
        self._row = row

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, _query: str, _params: tuple = ()) -> _Result:
        return _Result(self._row)


def _patch_session(monkeypatch: object, fake: _FakeSession) -> None:
    monkeypatch.setattr(cassandra_core, "get_cassandra_session", lambda: fake)
    cassandra_core.prepare_cached.cache_clear()


def test_a_naive_stored_timestamp_comes_back_tz_aware(monkeypatch: object) -> None:
    """The exact shape the Cassandra driver hands back for a `timestamp` column."""
    naive = datetime(2026, 8, 30, 12, 0, 0)  # noqa: DTZ001 -- simulating the driver's own naive value
    row = SimpleNamespace(scale_score=0.42, scale_updated_at=naive)
    _patch_session(monkeypatch, _FakeSession(row))

    score, updated_at = service_profile_store.get_stored_scale_signal("forum-topic:15401")

    assert score == 0.42
    assert updated_at is not None
    assert updated_at.tzinfo is not None
    # Reinterpreted as UTC, not shifted -- the driver's naive value IS the UTC wall time.
    assert updated_at == naive.replace(tzinfo=UTC)


def test_an_already_aware_stored_timestamp_is_left_untouched(monkeypatch: object) -> None:
    """A row that already carries tzinfo (e.g. a freshly-written one) is returned as-is."""
    aware = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
    row = SimpleNamespace(scale_score=0.1, scale_updated_at=aware)
    _patch_session(monkeypatch, _FakeSession(row))

    _score, updated_at = service_profile_store.get_stored_scale_signal("forum-topic:1")

    assert updated_at == aware


def test_no_stored_row_returns_none_none(monkeypatch: object) -> None:
    """No stored scale row at all -- must not raise on a None row."""
    _patch_session(monkeypatch, _FakeSession(None))

    score, updated_at = service_profile_store.get_stored_scale_signal("forum-topic:404")

    assert (score, updated_at) == (None, None)


def test_the_normalized_value_no_longer_crashes_the_real_staleness_check(
    monkeypatch: object,
) -> None:
    """End-to-end regression: the exact subtraction that crashed poll_forum_topics."""
    from datetime import timedelta

    from app.core import config
    from app.modules.newspaper.ingest_signal import _resolve_stale_scale_signal

    naive = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(
        days=config.SERVICE_SCALE_REFRESH_DAYS - 1
    )
    row = SimpleNamespace(scale_score=0.7, scale_updated_at=naive)
    _patch_session(monkeypatch, _FakeSession(row))

    # Must not raise -- and since the (now-aware) timestamp is inside the
    # refresh window, the stored score is returned without a fresh resolve.
    result = _resolve_stale_scale_signal(
        service_id="forum-topic:15401",
        display_name="Algorand Forum",
        source_url="https://forum.algorand.co/t/x/15401",
        outbound_links=[],
    )
    assert result == 0.7
