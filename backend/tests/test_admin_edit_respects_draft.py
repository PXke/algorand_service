"""Editing a drafted article's content must never re-add it to the public feed as a side effect -- feed membership is set_article_draft's job exclusively."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore


class _Result:
    def __init__(self, row: Any = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._row = row

    def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row
        return self._row


class _FakeSession:
    def __init__(self, *, published_at: datetime | None, draft: bool) -> None:
        self._row = SimpleNamespace(published_at=published_at, draft=draft)
        self.content_updates: list[tuple] = []
        self.feed_inserts: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        if q.startswith("SELECT published_at, draft"):
            return _Result(self._row)
        if q.startswith("UPDATE algorand_platform.articles_by_id SET title"):
            self.content_updates.append(tuple(params))
        elif q.startswith("INSERT INTO algorand_platform.articles_feed"):
            self.feed_inserts.append(tuple(params))
        return _Result(None)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:  # noqa: ANN401
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def _article(article_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        article_id=article_id,
        service_id="svc",
        tags=["a"],
        image_url="https://example.com/img.png",
        source_url="https://example.com/",
    )


def test_editing_a_draft_updates_content_but_skips_the_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A drafted article stays out of articles_feed after a content edit."""
    article_id = str(uuid4())
    fake = _FakeSession(published_at=datetime.now(tz=UTC), draft=True)
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    store._write_article(_article(article_id), "New Title", "New summary", "New body")

    assert len(fake.content_updates) == 1
    assert fake.feed_inserts == []


def test_editing_a_live_article_still_updates_the_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unchanged behavior for a normal (non-draft) live article edit."""
    article_id = str(uuid4())
    fake = _FakeSession(published_at=datetime.now(tz=UTC), draft=False)
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    store._write_article(_article(article_id), "New Title", "New summary", "New body")

    assert len(fake.content_updates) == 1
    assert len(fake.feed_inserts) == 1
