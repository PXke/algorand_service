"""admin_source_store.load_active_sources: bounded read, active-only filtering, and the fail-CLOSED contract a recompose-time caller depends on (design doc section 3 / CLAUDE.md invariant #8 -- an error must never look like "no sources")."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.modules.newspaper.admin_source_store import AdminSource, load_active_sources

_ARTICLE_ID = "11111111-1111-1111-1111-111111111111"


def _row(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "article_id": _ARTICLE_ID,
        "source_id": "22222222-2222-2222-2222-222222222222",
        "added_by": "0xADMIN",
        "label": "Exclusive interview with the Sprout creator, 2026-09-02",
        "kind": "text",
        "attribution_url": "",
        "content": "The creator said...",
        "status": "active",
        "added_at": datetime(2026, 9, 2, tzinfo=UTC),
        "removed_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeSession:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows
        self.captured_params: tuple | None = None

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, _stmt: object, params: tuple) -> list[SimpleNamespace]:
        self.captured_params = params
        return self._rows


def _patch_session(monkeypatch: pytest.MonkeyPatch, rows: list[SimpleNamespace]) -> _FakeSession:
    import app.core.cassandra as c

    session = _FakeSession(rows)
    monkeypatch.setattr(c, "get_cassandra_session", lambda: session)
    c.prepare_cached.cache_clear()
    return session


def test_load_active_sources_round_trips_row_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every stored field survives the round trip into an AdminSource."""
    session = _patch_session(monkeypatch, [_row()])
    out = load_active_sources(_ARTICLE_ID)
    assert len(out) == 1
    src = out[0]
    assert isinstance(src, AdminSource)
    assert src.source_id == "22222222-2222-2222-2222-222222222222"
    assert src.added_by == "0xADMIN"
    assert src.label == "Exclusive interview with the Sprout creator, 2026-09-02"
    assert src.kind == "text"
    assert src.content == "The creator said..."
    assert src.added_at == datetime(2026, 9, 2, tzinfo=UTC)
    assert str(session.captured_params[0]) == _ARTICLE_ID


def test_load_active_sources_filters_out_removed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """A soft-removed row (status='removed') is excluded -- it must not silently reappear as active."""
    rows = [
        _row(source_id="active-1", status="active"),
        _row(source_id="removed-1", status="removed"),
    ]
    _patch_session(monkeypatch, rows)
    out = load_active_sources(_ARTICLE_ID)
    assert [s.source_id for s in out] == ["active-1"]


def test_load_active_sources_empty_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No rows attached is the normal case: an empty list, not an exception (CLAUDE.md invariant #8's other half)."""
    _patch_session(monkeypatch, [])
    assert load_active_sources(_ARTICLE_ID) == []


def test_load_active_sources_caps_to_the_requested_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """More active rows than `limit` are capped -- newest-first ordering (clustering order) decides which survive."""
    rows = [_row(source_id=f"s{i}", status="active") for i in range(5)]
    _patch_session(monkeypatch, rows)
    out = load_active_sources(_ARTICLE_ID, limit=3)
    assert len(out) == 3
    assert [s.source_id for s in out] == ["s0", "s1", "s2"]


def test_load_active_sources_bad_article_id_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed article_id can't be a UUID -- returns [] rather than raising, same as an empty article_id."""
    session = _patch_session(monkeypatch, [])
    assert load_active_sources("not-a-uuid") == []
    assert load_active_sources("") == []
    assert session.captured_params is None  # never even reached Cassandra


def test_load_active_sources_read_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine Cassandra read error RAISES -- this function is deliberately not best-effort (unlike investigation_store.load_investigation_trace), because a recompose-time caller must fail CLOSED, not silently treat an error as 'no sources' (design doc section 3, CLAUDE.md invariant #8)."""
    import app.core.cassandra as c

    class _BoomSession:
        def prepare(self, cql: str) -> str:
            return cql

        def execute(self, *_a: object, **_kw: object) -> None:
            raise ConnectionError("cassandra down")

    monkeypatch.setattr(c, "get_cassandra_session", lambda: _BoomSession())
    c.prepare_cached.cache_clear()

    with pytest.raises(ConnectionError):
        load_active_sources(_ARTICLE_ID)
