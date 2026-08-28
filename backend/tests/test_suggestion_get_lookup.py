"""CassandraSuggestionStore.get's id-based lookup (suggestions_by_id).

2026-08-28 perf audit (same audit as migration 087 / suggestions_by_txid):
SuggestionStmts.GET used to run `WHERE status = ? AND suggestion_id = ?
ALLOW FILTERING` against suggestions_by_status. status is the partition key,
but the clustering columns are (created_at, suggestion_id) in that order, so
filtering on suggestion_id without also constraining created_at skips a
clustering column and forces ALLOW FILTERING even though the read is scoped
to a single partition. Migration 089 adds suggestions_by_id (suggestion_id
uuid PRIMARY KEY, full row denormalized onto it), dual-written alongside
every suggestions_by_status INSERT, and SuggestionStmts.GET now does a
direct point lookup against it. These tests check the new lookup-table-based
get() gives the same answer the old ALLOW FILTERING query did, for both an
existing and a non-existing suggestion_id, and that insert() actually
populates the lookup table (otherwise get() would silently always return
None).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.modules.suggestions.models.domain import StoredSuggestion
from app.modules.suggestions.stores.cassandra import CassandraSuggestionStore


class _Result:
    def __init__(self, row: Any = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._row = row

    def one(self) -> Any:  # noqa: ANN401
        return self._row


class _Row:
    def __init__(
        self,
        suggestion_id: Any,  # noqa: ANN401
        wallet_address: str,
        title: str,
        body: str,
        submission_txid: str,
        created_at: datetime,
        status: str,
    ) -> None:
        self.suggestion_id = suggestion_id
        self.wallet_address = wallet_address
        self.title = title
        self.body = body
        self.submission_txid = submission_txid
        self.created_at = created_at
        self.status = status


class _FakeSession:
    """Fake Cassandra session tracking writes and answering GET lookups.

    Tracks writes to suggestions_by_status / suggestions_by_txid /
    suggestions_by_id and answers GET lookups purely from what's been
    inserted into suggestions_by_id -- exactly like a real point lookup
    against that table would.
    """

    def __init__(self) -> None:
        self.status_inserts: list[tuple] = []
        self.txid_rows: dict[str, tuple] = {}
        self.by_id_rows: dict[Any, tuple] = {}

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        params = tuple(params)
        if q.startswith("INSERT INTO algorand_platform.suggestions_by_status"):
            self.status_inserts.append(params)
            return _Result(None)
        if q.startswith("INSERT INTO algorand_platform.suggestions_by_txid"):
            submission_txid, suggestion_id = params[0], params[1]
            self.txid_rows[submission_txid] = (suggestion_id,)
            return _Result(None)
        if q.startswith("INSERT INTO algorand_platform.suggestions_by_id"):
            (
                suggestion_id,
                status,
                created_at,
                wallet_address,
                title,
                body,
                submission_txid,
            ) = params
            self.by_id_rows[suggestion_id] = (
                suggestion_id,
                wallet_address,
                title,
                body,
                submission_txid,
                created_at,
                status,
            )
            return _Result(None)
        if q == (
            "SELECT suggestion_id, wallet_address, title, body, submission_txid, "
            "created_at, status FROM algorand_platform.suggestions_by_id "
            "WHERE suggestion_id = ?"
        ):
            row = self.by_id_rows.get(params[0])
            return _Result(_Row(*row) if row else None)
        if q == (
            "SELECT suggestion_id FROM algorand_platform.suggestions_by_txid "
            "WHERE submission_txid = ?"
        ):
            row = self.txid_rows.get(params[0])
            return _Result(_Row(row[0], "", "", "", "", None, "") if row else None)
        # Old-shape ALLOW FILTERING query against suggestions_by_status must no
        # longer be issued by get() at all.
        assert "ALLOW FILTERING" not in q, f"unexpected ALLOW FILTERING query: {q}"
        return _Result(None)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeSession) -> None:
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def _item(txid: str) -> StoredSuggestion:
    return StoredSuggestion(
        suggestion_id=str(uuid4()),
        wallet_address="W" * 58,
        title="Add dark mode",
        body="Please add a dark mode theme.",
        submission_txid=txid,
        status="open",
        created_at_epoch=int(datetime.now(tz=UTC).timestamp()),
    )


def test_get_returns_the_suggestion_for_an_existing_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A suggestion_id that was actually inserted is returned in full.

    Same answer the old ALLOW FILTERING scan gave, now via a lookup-table
    point read.
    """
    fake = _FakeSession()
    _patch(monkeypatch, fake)
    item = _item("A" * 52)
    CassandraSuggestionStore().insert(item)

    fetched = CassandraSuggestionStore().get(item.suggestion_id)

    assert fetched is not None
    assert fetched.suggestion_id == item.suggestion_id
    assert fetched.title == item.title
    assert fetched.body == item.body
    assert fetched.wallet_address == item.wallet_address
    assert fetched.submission_txid == item.submission_txid
    assert fetched.status == item.status


def test_get_returns_none_for_a_never_inserted_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A suggestion_id nothing has ever inserted is reported as not found."""
    fake = _FakeSession()
    _patch(monkeypatch, fake)
    CassandraSuggestionStore().insert(_item("B" * 52))

    assert CassandraSuggestionStore().get(str(uuid4())) is None


def test_get_returns_none_for_a_malformed_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-UUID suggestion_id short-circuits to None without querying Cassandra."""
    fake = _FakeSession()
    _patch(monkeypatch, fake)

    assert CassandraSuggestionStore().get("not-a-uuid") is None


def test_insert_dual_writes_the_id_lookup_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """insert() must populate suggestions_by_id alongside suggestions_by_status.

    Otherwise get()'s new lookup-table read would silently always miss.
    """
    fake = _FakeSession()
    _patch(monkeypatch, fake)
    item = _item("C" * 52)

    CassandraSuggestionStore().insert(item)

    assert len(fake.status_inserts) == 1
    from uuid import UUID

    assert UUID(item.suggestion_id) in fake.by_id_rows
    row = fake.by_id_rows[UUID(item.suggestion_id)]
    # (suggestion_id, wallet_address, title, body, submission_txid, created_at, status)
    assert row[1] == item.wallet_address
    assert row[2] == item.title
    assert row[3] == item.body
    assert row[4] == item.submission_txid
    assert row[6] == item.status
