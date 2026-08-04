"""dead_end_queue_row_domain: one-click permanent domain reject from a publish_queue row, without hunting for the same domain through the paginated Domains tab."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.core.statements import PublishQueueStmts
from app.modules.admin.stores.cassandra import AdminCassandraStore

_QUEUE_ID = "00000000-0000-0000-0000-000000000002"
_CREATED_AT = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


class _FakeSession:
    def __init__(self, *, row: object) -> None:
        self._row = row

    def execute(self, stmt: object, _params: tuple = ()) -> object:
        if stmt is PublishQueueStmts.GET_ROW:
            return SimpleNamespace(one=lambda: self._row)
        return SimpleNamespace(one=lambda: None)

    def prepare(self, cql: str) -> str:
        return cql


def _row(*, scrape_url: str = "https://kryptonurd.com/blog/x", status: str = "aborted") -> SimpleNamespace:
    return SimpleNamespace(
        queue_id=_QUEUE_ID,
        status=status,
        priority=100,
        topic="content_update",
        publish_kind="content_update",
        service_id="kryptonurd-com",
        display_name="Kryptonurd",
        scrape_url=scrape_url,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


def test_dead_ends_the_domain_resolved_from_scrape_url() -> None:
    """Resolves the eTLD+1 domain from the row's scrape_url and permanently rejects it -- reachable regardless of the row's status (an aborted-by-writer row is exactly the Kryptonurd case, not just pending ones)."""
    fake = _FakeSession(row=_row())
    store = AdminCassandraStore()
    with (
        patch("app.core.cassandra.get_cassandra_session", return_value=fake),
        patch.object(store, "reject_domain_source") as mock_reject,
    ):
        result = store.dead_end_queue_row_domain(_QUEUE_ID, wallet="0xADMIN")

    assert result == {"queue_id": _QUEUE_ID, "domain": "kryptonurd.com"}
    mock_reject.assert_called_once_with(
        domain="kryptonurd.com",
        wallet="0xADMIN",
        source_url_hint="https://kryptonurd.com/blog/x",
    )


def test_returns_none_for_unknown_queue_id() -> None:
    """A queue_id with no matching row returns None without calling reject_domain_source."""
    fake = _FakeSession(row=None)
    store = AdminCassandraStore()
    with (
        patch("app.core.cassandra.get_cassandra_session", return_value=fake),
        patch.object(store, "reject_domain_source") as mock_reject,
    ):
        result = store.dead_end_queue_row_domain(_QUEUE_ID, wallet="0xADMIN")
    assert result is None
    mock_reject.assert_not_called()


def test_returns_none_when_scrape_url_has_no_resolvable_domain() -> None:
    """A row with no scrape_url (e.g. an editorial-brief row that was never scraped from a domain) skips the reject instead of writing a bogus domain_tracking row."""
    fake = _FakeSession(row=_row(scrape_url=""))
    store = AdminCassandraStore()
    with (
        patch("app.core.cassandra.get_cassandra_session", return_value=fake),
        patch.object(store, "reject_domain_source") as mock_reject,
    ):
        result = store.dead_end_queue_row_domain(_QUEUE_ID, wallet="0xADMIN")
    assert result is None
    mock_reject.assert_not_called()


def test_rejects_malformed_queue_id() -> None:
    """A non-UUID queue_id returns None without touching Cassandra."""
    fake = _FakeSession(row=_row())
    store = AdminCassandraStore()
    with (
        patch("app.core.cassandra.get_cassandra_session", return_value=fake),
        patch.object(store, "reject_domain_source") as mock_reject,
    ):
        result = store.dead_end_queue_row_domain("not-a-uuid", wallet="0xADMIN")
    assert result is None
    mock_reject.assert_not_called()


def test_dead_end_works_regardless_of_row_status() -> None:
    """Unlike compose-next, dead-ending a domain isn't gated to pending rows -- done/aborted rows (the common Kryptonurd shape) must work too."""
    fake = _FakeSession(row=_row(status="done"))
    store = AdminCassandraStore()
    with (
        patch("app.core.cassandra.get_cassandra_session", return_value=fake),
        patch.object(store, "reject_domain_source") as mock_reject,
    ):
        result = store.dead_end_queue_row_domain(_QUEUE_ID, wallet="0xADMIN")
    assert result == {"queue_id": _QUEUE_ID, "domain": "kryptonurd.com"}
    mock_reject.assert_called_once()


