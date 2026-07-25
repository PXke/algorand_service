"""A compose_sessions row stuck in researching/writing past the staleness window (the compose crashed before its own checkpoint finalizers could mark it error/fallback) must be reaped to "stale", not left looking in-progress forever until the table's TTL quietly drops it."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.modules.ai.tool_insights_store import reap_stale_compose_sessions


class _Row:
    def __init__(self, created_at: datetime, session_id: str, status: str) -> None:
        self.created_at = created_at
        self.session_id = session_id
        self.status = status


def test_reaps_old_non_terminal_rows(
    monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001 -- name must match the real callee's keyword arg
    fake_cassandra_session: MagicMock,
) -> None:
    now = datetime.now(tz=UTC)
    rows = [
        _Row(now - timedelta(minutes=120), "old-researching", "researching"),
        _Row(now - timedelta(minutes=5), "recent-researching", "researching"),
        _Row(now - timedelta(minutes=120), "old-ok", "ok"),
    ]
    fake_cassandra_session.execute.side_effect = [rows, None]

    result = reap_stale_compose_sessions(stale_minutes=60)

    assert result == {"checked": 3, "reaped": 1}
    mark_call = fake_cassandra_session.execute.call_args_list[1]
    assert mark_call.args[1][3] == "old-researching"


def test_no_rows_is_a_noop(fake_cassandra_session: MagicMock) -> None:
    fake_cassandra_session.execute.return_value = []
    result = reap_stale_compose_sessions(stale_minutes=60)
    assert result == {"checked": 0, "reaped": 0}


def test_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: (_ for _ in ()).throw(RuntimeError("cassandra down")),
    )
    assert reap_stale_compose_sessions(stale_minutes=60) == {"checked": 0, "reaped": 0}
