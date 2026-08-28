"""CassandraSuggestionStore's duplicate-txid guard (has_submission_txid).

2026-08-28 perf audit: HAS_TXID used to run `WHERE submission_txid = ? ALLOW
FILTERING` against suggestions_by_status -- a cluster-wide scan on every
suggestion submission. Migration 087 adds suggestions_by_txid (submission_txid
text PRIMARY KEY), dual-written alongside every suggestions_by_status INSERT,
and HAS_TXID now does a direct point lookup against it. These tests check the
new lookup-table-based check gives the same true/false answers the old scan
did, for both an existing and a non-existing txid, and that insert() actually
populates the lookup table (otherwise the check would silently always say
"not used").
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


class _FakeSession:
    """Fake Cassandra session tracking writes and answering HAS_TXID lookups.

    Tracks writes to suggestions_by_status / suggestions_by_txid and answers
    HAS_TXID lookups purely from what's been inserted into the txid table --
    exactly like a real point lookup against that table would.
    """

    def __init__(self) -> None:
        self.status_inserts: list[tuple] = []
        self.txid_rows: dict[str, tuple] = {}

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
        if q == (
            "SELECT suggestion_id FROM algorand_platform.suggestions_by_txid "
            "WHERE submission_txid = ?"
        ):
            row = self.txid_rows.get(params[0])
            return _Result(_Row(row[0]) if row else None)
        # Old-shape ALLOW FILTERING query against suggestions_by_status must no
        # longer be issued by has_submission_txid at all.
        assert "ALLOW FILTERING" not in q, f"unexpected ALLOW FILTERING query: {q}"
        return _Result(None)


class _Row:
    def __init__(self, suggestion_id: Any) -> None:  # noqa: ANN401
        self.suggestion_id = suggestion_id


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


def test_has_submission_txid_true_for_an_existing_txid(monkeypatch: pytest.MonkeyPatch) -> None:
    """A txid that was actually inserted is reported as used.

    Same answer the old ALLOW FILTERING scan gave, now via a lookup-table
    point read.
    """
    fake = _FakeSession()
    _patch(monkeypatch, fake)
    txid = "E" * 52
    CassandraSuggestionStore().insert(_item(txid))

    assert CassandraSuggestionStore().has_submission_txid(txid) is True


def test_has_submission_txid_false_for_a_never_used_txid(monkeypatch: pytest.MonkeyPatch) -> None:
    """A txid nothing has ever inserted is reported as not used."""
    fake = _FakeSession()
    _patch(monkeypatch, fake)
    CassandraSuggestionStore().insert(_item("F" * 52))

    assert CassandraSuggestionStore().has_submission_txid("N" * 52) is False


def test_insert_dual_writes_the_txid_lookup_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """insert() must populate suggestions_by_txid alongside suggestions_by_status.

    Otherwise has_submission_txid's new lookup-table read would silently
    always miss.
    """
    fake = _FakeSession()
    _patch(monkeypatch, fake)
    txid = "G" * 52
    item = _item(txid)

    CassandraSuggestionStore().insert(item)

    assert len(fake.status_inserts) == 1
    assert txid in fake.txid_rows
    assert str(fake.txid_rows[txid][0]) == item.suggestion_id


def test_insert_raises_on_duplicate_txid(monkeypatch: pytest.MonkeyPatch) -> None:
    """The duplicate guard on insert() itself still fires off the new lookup table."""
    from app.modules.suggestions.models.domain import SuggestionError

    fake = _FakeSession()
    _patch(monkeypatch, fake)
    txid = "H" * 52
    CassandraSuggestionStore().insert(_item(txid))

    with pytest.raises(SuggestionError) as exc:
        CassandraSuggestionStore().insert(_item(txid))
    assert exc.value.code == "duplicate_txid"
    # The rejected second submission must not have reached suggestions_by_status.
    assert len(fake.status_inserts) == 1
