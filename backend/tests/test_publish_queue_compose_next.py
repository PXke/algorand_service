"""bump_queue_priority: pin a pending queue row to the front of the drain's priority order, without touching the pacing clock or daily cap that gate when the drain runs at all."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from app.core.statements import PublishQueueStmts
from app.modules.admin.stores.cassandra import AdminCassandraStore
from tests.conftest import stmt_cql

_QUEUE_ID = "00000000-0000-0000-0000-000000000001"
_CREATED_AT = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

_GET_ROW = stmt_cql(PublishQueueStmts, "GET_ROW")
_MAX_PENDING_PRIORITY = stmt_cql(PublishQueueStmts, "MAX_PENDING_PRIORITY")
_UPDATE_PRIORITY = stmt_cql(PublishQueueStmts, "UPDATE_PRIORITY")
_DELETE_PENDING = stmt_cql(PublishQueueStmts, "DELETE_PENDING")
_INSERT_PENDING = stmt_cql(PublishQueueStmts, "INSERT_PENDING")


class _FakeSession:
    def __init__(self, *, row: object, max_row: object) -> None:
        self._row = row
        self._max_row = max_row
        self.calls: list[tuple[object, tuple]] = []

    def execute(self, stmt: object, params: tuple = ()) -> object:
        self.calls.append((stmt, params))
        if stmt == _GET_ROW:
            return SimpleNamespace(one=lambda: self._row)
        if stmt == _MAX_PENDING_PRIORITY:
            return SimpleNamespace(one=lambda: self._max_row)
        return SimpleNamespace(one=lambda: None)

    def prepare(self, cql: str) -> str:
        return cql


def _pending_row(*, priority: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        queue_id=_QUEUE_ID,
        status="pending",
        priority=priority,
        topic="editorial_assignment",
        publish_kind="editorial_assignment",
        service_id="editorial-brief:abc",
        display_name="Test brief",
        scrape_url="editorial://brief/abc",
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


def _run(fake: _FakeSession, queue_id: str = _QUEUE_ID) -> dict | None:
    store = AdminCassandraStore()
    with (
        patch("app.core.cassandra.get_cassandra_session", return_value=fake),
        patch("app.core.cassandra.prepare_cached", lambda cql: cql),
    ):
        return store.bump_queue_priority(queue_id)


def test_bump_sets_priority_above_current_max() -> None:
    """The row's new priority is one above the current highest pending priority."""
    fake = _FakeSession(row=_pending_row(priority=100), max_row=SimpleNamespace(priority=307))
    result = _run(fake)
    assert result == {"queue_id": _QUEUE_ID, "priority": 308}


def test_bump_updates_base_table_and_resyncs_pending_index() -> None:
    """Bumping writes UPDATE_PRIORITY on the base table, then deletes the old publish_queue_pending clustering row and inserts the new one (required since priority is a clustering column)."""
    fake = _FakeSession(row=_pending_row(priority=100), max_row=SimpleNamespace(priority=307))
    _run(fake)

    stmts_called = [c[0] for c in fake.calls]
    assert _UPDATE_PRIORITY in stmts_called
    assert _DELETE_PENDING in stmts_called
    assert _INSERT_PENDING in stmts_called

    delete_call = next(c for c in fake.calls if c[0] == _DELETE_PENDING)
    assert delete_call[1] == ("pending", 100, _CREATED_AT, UUID(_QUEUE_ID))

    insert_call = next(c for c in fake.calls if c[0] == _INSERT_PENDING)
    assert insert_call[1] == (
        "pending",
        308,
        _CREATED_AT,
        UUID(_QUEUE_ID),
        "editorial-brief:abc",
        "editorial_assignment",
        "editorial_assignment",
    )


def test_bump_handles_already_highest_priority_row() -> None:
    """Bumping the row that's already the max still strictly increases its priority (no accidental tie)."""
    fake = _FakeSession(row=_pending_row(priority=307), max_row=SimpleNamespace(priority=307))
    result = _run(fake)
    assert result == {"queue_id": _QUEUE_ID, "priority": 308}


def test_bump_handles_empty_pending_index() -> None:
    """No other pending rows (MAX_PENDING_PRIORITY returns None) still produces a valid bump."""
    fake = _FakeSession(row=_pending_row(priority=50), max_row=None)
    result = _run(fake)
    assert result == {"queue_id": _QUEUE_ID, "priority": 51}


def test_bump_returns_none_for_unknown_queue_id() -> None:
    """A queue_id with no matching row returns None, no writes issued."""
    fake = _FakeSession(row=None, max_row=SimpleNamespace(priority=100))
    result = _run(fake)
    assert result is None
    assert not any(c[0] == _UPDATE_PRIORITY for c in fake.calls)


def test_bump_returns_none_for_non_pending_row() -> None:
    """A row that already resolved (status != pending) cannot be bumped."""
    row = _pending_row(priority=100)
    row.status = "done"
    fake = _FakeSession(row=row, max_row=SimpleNamespace(priority=307))
    result = _run(fake)
    assert result is None
    assert not any(c[0] == _UPDATE_PRIORITY for c in fake.calls)


def test_bump_rejects_malformed_queue_id() -> None:
    """A non-UUID queue_id returns None without touching Cassandra."""
    fake = _FakeSession(row=_pending_row(), max_row=SimpleNamespace(priority=100))
    result = _run(fake, "not-a-uuid")
    assert result is None
    assert fake.calls == []
