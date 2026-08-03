"""Editorial briefs: is_special_edition round-trips through create/list/get."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.core.statements import EditorialBriefStmts
from app.modules.admin.stores.cassandra import AdminCassandraStore


class _FakeSession:
    def __init__(self, *, row: object = None, rows: list[object] | None = None) -> None:
        self._row = row
        self._rows = rows
        self.calls: list[tuple[object, tuple]] = []

    def execute(self, stmt: object, params: tuple = ()) -> object:
        self.calls.append((stmt, params))
        if self._rows is not None:
            return self._rows
        return SimpleNamespace(one=lambda: self._row)

    def prepare(self, cql: str) -> str:
        return cql


def _row(*, is_special_edition: bool | None) -> SimpleNamespace:
    now = datetime.now(tz=UTC)
    return SimpleNamespace(
        brief_id="00000000-0000-0000-0000-000000000001",
        title="State of Algorand DeFi",
        body_markdown="Angle: quarterly deep dive.",
        keywords="defi, algorand",
        status="active",
        wallet_address="ABC",
        created_at=now,
        updated_at=now,
        refresh_every_days=30,
        last_run_at=None,
        linked_article_id=None,
        is_special_edition=is_special_edition,
    )


def test_create_brief_inserts_is_special_edition_column() -> None:
    """create_brief passes is_special_edition through as the last INSERT column."""
    fake = _FakeSession()
    store = AdminCassandraStore()
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        store.create_brief(
            title="State of Algorand DeFi",
            body_markdown="Angle: quarterly deep dive.",
            keywords="defi, algorand",
            status="active",
            wallet_address="ABC",
            refresh_every_days=30,
            is_special_edition=True,
        )
    stmt, params = fake.calls[0]
    assert stmt is EditorialBriefStmts.INSERT
    assert params[-1] is True


def test_create_brief_defaults_is_special_edition_false() -> None:
    """create_brief defaults is_special_edition to False when the caller omits it."""
    fake = _FakeSession()
    store = AdminCassandraStore()
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        store.create_brief(
            title="Wallet roundup",
            body_markdown="Cover download links.",
            keywords="wallet",
            status="active",
            wallet_address="ABC",
        )
    _stmt, params = fake.calls[0]
    assert params[-1] is False


def test_get_brief_surfaces_is_special_edition_true() -> None:
    """get_brief reports is_special_edition=True from the row."""
    fake = _FakeSession(row=_row(is_special_edition=True))
    store = AdminCassandraStore()
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        brief = store.get_brief("00000000-0000-0000-0000-000000000001")
    assert brief is not None
    assert brief["is_special_edition"] is True


def test_get_brief_null_column_reads_as_false() -> None:
    """A null is_special_edition column (pre-migration rows) reads as False, not None."""
    fake = _FakeSession(row=_row(is_special_edition=None))
    store = AdminCassandraStore()
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        brief = store.get_brief("00000000-0000-0000-0000-000000000001")
    assert brief is not None
    assert brief["is_special_edition"] is False


def test_list_briefs_surfaces_is_special_edition() -> None:
    """list_briefs includes is_special_edition per row."""
    fake = _FakeSession(rows=[_row(is_special_edition=True), _row(is_special_edition=False)])
    store = AdminCassandraStore()
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        items = store.list_briefs()
    assert [item["is_special_edition"] for item in items] == [True, False]
