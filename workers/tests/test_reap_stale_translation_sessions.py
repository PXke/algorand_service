"""A translation_sessions row stuck 'running' past the staleness window (a crash mid-language skipped the on_language_done/on_language_error callback) must be reaped to "stale", not left looking in-progress forever until the table's TTL quietly drops it."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.modules.ai.translation_session_store import (
    finish_translation_session,
    reap_stale_translation_sessions,
    start_translation_session,
)


class _Row:
    def __init__(self, started_at: datetime, session_id: str, status: str) -> None:
        self.started_at = started_at
        self.session_id = session_id
        self.status = status


def test_reaps_old_non_terminal_rows(fake_cassandra_session: MagicMock) -> None:
    """Reaps only 'running' rows older than the staleness window, leaving recent and terminal rows alone."""
    now = datetime.now(tz=UTC)
    rows = [
        _Row(now - timedelta(minutes=200), "old-running", "running"),
        _Row(now - timedelta(minutes=5), "recent-running", "running"),
        _Row(now - timedelta(minutes=200), "old-ok", "ok"),
    ]
    fake_cassandra_session.execute.side_effect = [rows, None]

    result = reap_stale_translation_sessions(stale_minutes=180)

    assert result == {"checked": 3, "reaped": 1}
    mark_call = fake_cassandra_session.execute.call_args_list[1]
    assert mark_call.args[1][3] == "old-running"


def test_no_rows_is_a_noop(fake_cassandra_session: MagicMock) -> None:
    """No rows to check is a no-op that reaps nothing."""
    fake_cassandra_session.execute.return_value = []
    result = reap_stale_translation_sessions(stale_minutes=180)
    assert result == {"checked": 0, "reaped": 0}


def test_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Cassandra failure is swallowed, returning a zeroed result instead of raising."""
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: (_ for _ in ()).throw(RuntimeError("cassandra down")),
    )
    assert reap_stale_translation_sessions(stale_minutes=180) == {"checked": 0, "reaped": 0}


def test_start_writes_a_running_row_and_returns_a_ref(fake_cassandra_session: MagicMock) -> None:
    """start_translation_session inserts a 'running' row and returns (session_id, started_at) for the matching finish call."""
    ref = start_translation_session("article-1", "fa")
    assert ref is not None
    session_id, started_at = ref
    insert_call = fake_cassandra_session.execute.call_args
    args = insert_call.args[1]
    assert args[3] == "article-1"
    assert args[4] == "fa"
    assert args[5] == "running"
    assert session_id is not None
    assert started_at is not None


def test_start_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Cassandra failure on start is swallowed, returning None rather than raising."""
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: (_ for _ in ()).throw(RuntimeError("cassandra down")),
    )
    assert start_translation_session("article-1", "fa") is None


def test_finish_updates_the_row_from_the_ref(fake_cassandra_session: MagicMock) -> None:
    """finish_translation_session updates the exact (bucket, started_at, session_id) the ref points at."""
    now = datetime.now(tz=UTC)
    ref = ("session-id", now)

    assert finish_translation_session(ref, status="ok") is True
    update_call = fake_cassandra_session.execute.call_args
    args = update_call.args[1]
    assert args[0] == "ok"
    assert args[5] == "session-id"


def test_finish_is_a_noop_with_no_ref(fake_cassandra_session: MagicMock) -> None:
    """A None ref (the start write itself failed) means there's no row to update -- finish must not raise or call Cassandra."""
    assert finish_translation_session(None, status="error", error="boom") is False
    fake_cassandra_session.execute.assert_not_called()
