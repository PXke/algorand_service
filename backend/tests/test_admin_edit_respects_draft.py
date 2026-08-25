"""Editing an article's content must never change its feed membership as a side effect -- that's set_article_draft's job exclusively.

Article-table consolidation Phase 5: `_write_article` now does a single
content-only UPDATE on the `articles` row (status/year/published_at, its
partition key, are read fresh and left untouched) -- there is no separate
feed-projection table left to conditionally sync, so a drafted article and a
live article are handled by the exact same code path now (previously, a
live article ALSO got a full articles_feed row upsert; a drafted one didn't,
guarded by an explicit branch -- root-caused live 2026-08-11 when that
branch was still missing).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore

# `articles`' column order (see algorand_shared.article_transitions._ARTICLES_COLUMNS).
_ARTICLES_COLUMNS = (
    "status", "year", "published_at", "article_id", "service_id", "title", "summary", "body",
    "image_url", "tags", "source_url", "trigger_txid", "trigger_round", "slug", "translations",
    "first_published_at", "updated_at", "prompt_version", "composed_by_model",
    "deleted_at", "status_updated_at", "interest_score", "approved_at",
)  # fmt: skip


class _Result:
    def __init__(self, row: Any = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._row = row

    def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row
        return self._row


class _FakeSession:
    def __init__(self, *, published_at: datetime | None, status: str) -> None:
        values: dict[str, object] = dict.fromkeys(_ARTICLES_COLUMNS)
        values.update(
            status=status,
            year=(published_at or datetime.now(tz=UTC)).year,
            published_at=published_at,
            image_url="https://example.com/img.png",
            updated_at=None,
        )
        self._row = SimpleNamespace(**values)
        self.content_updates: list[tuple] = []
        self.feed_inserts: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "FROM algorand_platform.articles WHERE article_id = ?" in q:
            return _Result(self._row)
        if q.startswith("UPDATE algorand_platform.articles SET title"):
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


def test_editing_a_draft_updates_content_and_never_touches_the_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drafted article's `articles` row (status='draft') gets its content updated in place; there is no separate feed table to leave untouched or accidentally resurrect."""
    article_id = str(uuid4())
    fake = _FakeSession(published_at=datetime.now(tz=UTC), status="draft")
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    store._write_article(_article(article_id), "New Title", "New summary", "New body")

    assert len(fake.content_updates) == 1
    assert fake.content_updates[0][0] == "New Title"
    assert fake.feed_inserts == []


def test_editing_a_live_article_also_never_touches_the_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live (status='published') article's content edit is the SAME in-place `articles` UPDATE -- status='published' is what makes it feed-visible, and this write never touches status, so no separate feed sync step is needed (unlike the pre-consolidation schema, where a live edit ALSO upserted a full articles_feed row)."""
    article_id = str(uuid4())
    fake = _FakeSession(published_at=datetime.now(tz=UTC), status="published")
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    store._write_article(_article(article_id), "New Title", "New summary", "New body")

    assert len(fake.content_updates) == 1
    assert fake.content_updates[0][0] == "New Title"
    assert fake.feed_inserts == []
