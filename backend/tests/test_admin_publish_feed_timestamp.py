"""Publishing to the feed must stamp release time, not compose time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    """The statement registry resolves *Stmts.* by calling get_cassandra_session().prepare(cql); return the CQL text so execute() can branch on it (SELECT vs INSERT vs UPDATE), matching the pattern already used in workers/tests/test_domain_status_sticky.py."""

    def __init__(self, feed_row: Any) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._feed_row = feed_row
        self.feed_inserts: list[tuple] = []
        self.published_at_updates: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "articles_by_id" in q:
            return _Result(self._feed_row)
        if q.startswith("INSERT INTO algorand_platform.articles_feed"):
            self.feed_inserts.append(tuple(params))
        elif q.startswith("UPDATE algorand_platform.articles_by_id SET published_at"):
            self.published_at_updates.append(tuple(params))
        return _Result(None)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:  # noqa: ANN401 -- duck-typed fake Cassandra session
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def test_publish_article_to_feed_stamps_release_time_not_compose_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held/review draft's articles_by_id.published_at was stamped at compose time — first release into the feed must stamp the real release moment on BOTH articles_feed and articles_by_id, not reuse the stale compose-time value (root-caused 2026-07-14: this let a held draft display the wrong timestamp and dodge the daily cap's published_at-windowed count)."""
    article_id = uuid4()
    compose_time = datetime.now(tz=UTC) - timedelta(hours=5)
    feed_row = SimpleNamespace(
        article_id=article_id,
        service_id="svc",
        title="Title",
        summary="Summary",
        published_at=compose_time,
        tags=["a", "b"],
        image_url="https://example.com/img.png",
        source_url="https://example.com/",
    )
    fake = _FakeSession(feed_row)
    _patch(monkeypatch, fake)
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", lambda *_a, **_kw: None)

    store = AdminCassandraStore()
    before = datetime.now(tz=UTC)
    result = store._publish_article_to_feed(str(article_id))
    after = datetime.now(tz=UTC)

    assert result is True
    assert len(fake.feed_inserts) == 1
    feed_published_at = fake.feed_inserts[0][1]
    assert feed_published_at != compose_time
    assert before <= feed_published_at <= after

    assert len(fake.published_at_updates) == 1
    assert fake.published_at_updates[0] == (feed_published_at, article_id)
