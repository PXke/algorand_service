"""Article version history: list (title/editor/reason/date) and full-content fetch per version, for the admin diff view."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore


class _Result:
    def __init__(self, rows: Any) -> None:  # noqa: ANN401
        self._rows = rows

    def one(self) -> Any:  # noqa: ANN401
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Any:  # noqa: ANN401
        return iter(self._rows)


class _FakeSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        if "AND version" in q:
            version = params[1]
            matches = [r for r in self._rows if r.version == version]
            return _Result(matches)
        return _Result(self._rows)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:  # noqa: ANN401
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def _version_row(version: int, title: str) -> SimpleNamespace:
    return SimpleNamespace(
        version=version,
        title=title,
        summary=f"summary v{version}",
        body=f"body v{version}",
        edit_reason="before_admin_edit",
        editor="admin:wallet",
        edited_at=datetime(2026, 8, 11, 0, 0, version, tzinfo=UTC),
    )


def test_list_article_versions_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Versions come back sorted newest (highest number) first, without body content."""
    rows = [_version_row(1, "First"), _version_row(3, "Third"), _version_row(2, "Second")]
    fake = _FakeSession(rows)
    _patch(monkeypatch, fake)

    items = AdminCassandraStore().list_article_versions(str(uuid4()))

    assert [it["version"] for it in items] == [3, 2, 1]
    assert "body" not in items[0]


def test_list_article_versions_invalid_id_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed article_id is a usage error, not a Cassandra call."""
    fake = _FakeSession([])
    _patch(monkeypatch, fake)
    assert AdminCassandraStore().list_article_versions("not-a-uuid") == []


def test_get_article_version_returns_full_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetching a specific version returns its full title/summary/body."""
    rows = [_version_row(1, "First"), _version_row(2, "Second")]
    fake = _FakeSession(rows)
    _patch(monkeypatch, fake)

    result = AdminCassandraStore().get_article_version(str(uuid4()), 1)

    assert result is not None
    assert result["title"] == "First"
    assert result["body"] == "body v1"


def test_get_article_version_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A version number that was never stored returns None, not an error."""
    fake = _FakeSession([_version_row(1, "First")])
    _patch(monkeypatch, fake)

    assert AdminCassandraStore().get_article_version(str(uuid4()), 99) is None
