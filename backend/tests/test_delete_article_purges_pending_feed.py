"""Hard-deleting an article must also drop any pending_feed_queue row waiting to release it -- otherwise the paced-release worker (and the admin "up next to publish" view) is left pointing at a phantom article_id."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore


class _Result:
    def __init__(self, row: Any = None, rows: list | None = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row
        return self._row

    def __iter__(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra ResultSet
        return iter(self._rows)


_ARTICLES_COLUMNS = (
    "status", "year", "published_at", "article_id", "service_id", "title", "summary", "body",
    "image_url", "tags", "source_url", "trigger_txid", "trigger_round", "slug", "translations",
    "first_published_at", "updated_at", "prompt_version", "composed_by_model",
    "deleted_at", "status_updated_at", "interest_score", "approved_at",
)  # fmt: skip


class _FakeSession:
    def __init__(self, *, published_at: datetime, pending_rows: list, article_id: Any = None) -> None:  # noqa: ANN401
        values: dict[str, object] = dict.fromkeys(_ARTICLES_COLUMNS)
        values.update(
            status="published",
            year=published_at.year,
            published_at=published_at,
            article_id=article_id,
        )
        self._article_row = SimpleNamespace(**values)
        self._pending_rows = pending_rows
        self.pending_deletes: list[tuple] = []
        self.articles_inserts: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "FROM algorand_platform.articles WHERE article_id = ?" in q:
            return _Result(self._article_row)
        if q.startswith("SELECT version FROM algorand_platform.article_versions"):
            return _Result(rows=[])
        if q.startswith(
            "SELECT bucket, interest_score, approved_at, article_id "
            "FROM algorand_platform.pending_feed_queue"
        ):
            return _Result(rows=list(self._pending_rows))
        if q.startswith("DELETE FROM algorand_platform.pending_feed_queue"):
            self.pending_deletes.append(tuple(params))
            return _Result(None)
        if q.startswith("INSERT INTO algorand_platform.articles ("):
            self.articles_inserts.append(tuple(params))
            return _Result(None)
        return _Result(None)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:  # noqa: ANN401 -- duck-typed fake Cassandra session
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def _stub_current_article() -> SimpleNamespace:
    return SimpleNamespace(title="Some Title", translations={}, slug="some-slug")


def test_delete_article_purges_its_pending_feed_queue_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending_feed_queue row matching the deleted article's id is removed by its full clustering key."""
    article_id = uuid4()
    other_id = uuid4()
    pending_rows = [
        SimpleNamespace(bucket="main", interest_score=0.0, approved_at=datetime(2026, 8, 10, tzinfo=UTC), article_id=article_id),
        SimpleNamespace(bucket="main", interest_score=1.5, approved_at=datetime(2026, 8, 11, tzinfo=UTC), article_id=other_id),
    ]
    fake = _FakeSession(published_at=datetime.now(tz=UTC), pending_rows=pending_rows, article_id=article_id)
    _patch(monkeypatch, fake)
    monkeypatch.setattr(AdminCassandraStore, "get_article", lambda self, aid: _stub_current_article())  # noqa: ARG005

    result = AdminCassandraStore().delete_article(str(article_id))

    assert result is True
    assert fake.pending_deletes == [("main", 0.0, datetime(2026, 8, 10, tzinfo=UTC), article_id)]
    # Deletion is an `articles` status transition to 'deleted' (tombstoned),
    # not a hard row delete.
    assert len(fake.articles_inserts) == 1
    values = dict(zip(_ARTICLES_COLUMNS, fake.articles_inserts[0], strict=True))
    assert values["status"] == "deleted"
    assert values["article_id"] == article_id
    assert values["deleted_at"] is not None


def test_delete_article_is_a_noop_on_pending_feed_when_article_not_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No matching pending_feed_queue row -- deletion proceeds without touching that table."""
    article_id = uuid4()
    other_id = uuid4()
    pending_rows = [
        SimpleNamespace(bucket="main", interest_score=1.5, approved_at=datetime(2026, 8, 11, tzinfo=UTC), article_id=other_id),
    ]
    fake = _FakeSession(published_at=datetime.now(tz=UTC), pending_rows=pending_rows)
    _patch(monkeypatch, fake)
    monkeypatch.setattr(AdminCassandraStore, "get_article", lambda self, aid: _stub_current_article())  # noqa: ARG005

    result = AdminCassandraStore().delete_article(str(article_id))

    assert result is True
    assert fake.pending_deletes == []
