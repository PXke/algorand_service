"""Admin-only draft toggle: pull a live article out of the feed reversibly, without touching its stored content."""

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
    def __init__(self, feed_row: Any) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._feed_row = feed_row
        self.draft_updates: list[tuple] = []
        self.draft_index_inserts: list[tuple] = []
        self.draft_index_deletes: list[tuple] = []
        self.feed_deletes: list[tuple] = []
        self.feed_inserts: list[tuple] = []
        self.slug_updates: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "articles_by_id" in q:
            return _Result(self._feed_row)
        if q.startswith("UPDATE algorand_platform.articles_by_id SET draft"):
            self.draft_updates.append(tuple(params))
        elif q.startswith("INSERT INTO algorand_platform.draft_articles"):
            self.draft_index_inserts.append(tuple(params))
        elif q.startswith("DELETE FROM algorand_platform.draft_articles"):
            self.draft_index_deletes.append(tuple(params))
        elif q.startswith("DELETE FROM algorand_platform.articles_feed"):
            self.feed_deletes.append(tuple(params))
        elif q.startswith("INSERT INTO algorand_platform.articles_feed"):
            self.feed_inserts.append(tuple(params))
        elif q.startswith("UPDATE algorand_platform.articles_feed SET slug"):
            self.slug_updates.append(tuple(params))
        return _Result(None)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:  # noqa: ANN401 -- duck-typed fake Cassandra session
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def _feed_row(article_id: Any, *, slug: str | None = "a-slug") -> Any:  # noqa: ANN401
    return SimpleNamespace(
        article_id=article_id,
        service_id="svc",
        title="Title",
        summary="Summary",
        published_at=datetime.now(tz=UTC),
        tags=["a", "b"],
        image_url="https://example.com/img.png",
        source_url="https://example.com/",
        slug=slug,
    )


def test_set_draft_true_removes_from_feed_and_indexes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drafting a live article deletes its articles_feed row and records it in the draft index, but never touches articles_by_id content."""
    article_id = uuid4()
    fake = _FakeSession(_feed_row(article_id))
    _patch(monkeypatch, fake)
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", lambda *a, **kw: None)  # noqa: ARG005
    # get_article's own full-row read (GET_FULL) isn't what this test is about
    # -- stub it so the fake session's simplified GET_FEED_ROW-shaped row
    # doesn't need every GET_FULL column too.
    monkeypatch.setattr(AdminCassandraStore, "get_article", lambda self, aid: object())  # noqa: ARG005

    result = AdminCassandraStore().set_article_draft(str(article_id), True)

    assert result is not None
    assert fake.draft_updates == [(True, article_id)]
    assert len(fake.draft_index_inserts) == 1
    assert fake.draft_index_inserts[0][0] == article_id
    assert len(fake.feed_deletes) == 1
    assert fake.feed_deletes[0][-1] == article_id
    assert fake.feed_inserts == []
    assert fake.draft_index_deletes == []


def test_set_draft_false_restores_the_same_feed_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restoring re-inserts articles_feed with the ORIGINAL published_at (not re-stamped) and re-claims the slug, and clears the draft index."""
    article_id = uuid4()
    row = _feed_row(article_id)
    fake = _FakeSession(row)
    _patch(monkeypatch, fake)
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", lambda *a, **kw: None)  # noqa: ARG005
    monkeypatch.setattr(AdminCassandraStore, "get_article", lambda self, aid: object())  # noqa: ARG005

    AdminCassandraStore().set_article_draft(str(article_id), False)

    assert fake.draft_updates == [(False, article_id)]
    assert fake.draft_index_deletes == [(article_id,)]
    assert fake.draft_index_inserts == []
    assert len(fake.feed_inserts) == 1
    inserted = fake.feed_inserts[0]
    assert inserted[1] == row.published_at  # unchanged -- a restore, not a republish
    assert inserted[2] == article_id
    assert len(fake.slug_updates) == 1
    assert fake.slug_updates[0][0] == "a-slug"


def test_set_draft_on_missing_article_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing to toggle -- no writes at all."""
    fake = _FakeSession(None)
    _patch(monkeypatch, fake)

    result = AdminCassandraStore().set_article_draft(str(uuid4()), True)

    assert result is None
    assert fake.draft_updates == []
