"""Publishing to the feed must stamp release time, not compose time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    """The statement registry resolves *Stmts.* by calling get_cassandra_session().prepare(cql); return the CQL text so execute() can branch on it (SELECT vs INSERT vs UPDATE), matching the pattern already used in workers/tests/test_domain_status_sticky.py."""

    def __init__(self, article_row: Any) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._article_row = article_row
        self.articles_inserts: list[tuple] = []
        self.articles_deletes: list[tuple] = []
        self.slug_updates: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "FROM algorand_platform.articles WHERE article_id = ?" in q:
            return _Result(self._article_row)
        if q.startswith("INSERT INTO algorand_platform.articles ("):
            self.articles_inserts.append(tuple(params))
        elif q.startswith("DELETE FROM algorand_platform.articles "):
            self.articles_deletes.append(tuple(params))
        elif q.startswith("UPDATE algorand_platform.articles SET slug"):
            self.slug_updates.append(tuple(params))
        return _Result(None)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:  # noqa: ANN401 -- duck-typed fake Cassandra session
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def _article_row(article_id: Any, *, published_at: datetime, slug: str | None) -> Any:  # noqa: ANN401
    values: dict[str, object] = dict.fromkeys(_ARTICLES_COLUMNS)
    values.update(
        status="backlog",
        year=published_at.year,
        published_at=published_at,
        article_id=article_id,
        service_id="svc",
        title="Title",
        summary="Summary",
        body="",
        tags=["a", "b"],
        image_url="https://example.com/img.png",
        source_url="https://example.com/",
        trigger_txid="",
        trigger_round=0,
        slug=slug,
    )
    return SimpleNamespace(**values)


def test_publish_article_to_feed_stamps_release_time_not_compose_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held/review draft's `articles` row published_at was stamped at compose time — first release must re-stamp the real release moment (root-caused 2026-07-14: this let a held draft display the wrong timestamp and dodge the daily cap's published_at-windowed count)."""
    article_id = uuid4()
    compose_time = datetime.now(tz=UTC) - timedelta(hours=5)
    row = _article_row(article_id, published_at=compose_time, slug="a-real-slug")
    fake = _FakeSession(row)
    _patch(monkeypatch, fake)
    pinged: list[dict] = []
    monkeypatch.setattr(
        "app.modules.seo.indexnow.ping_article",
        lambda *a, **kw: pinged.append({"args": a, "kwargs": kw}),
    )

    store = AdminCassandraStore()
    before = datetime.now(tz=UTC)
    result = store._publish_article_to_feed(str(article_id))
    after = datetime.now(tz=UTC)

    assert result is True
    assert len(fake.articles_inserts) == 1
    values = dict(zip(_ARTICLES_COLUMNS, fake.articles_inserts[0], strict=True))
    assert values["status"] == "published"
    assert values["published_at"] != compose_time
    assert before <= values["published_at"] <= after
    # The row's OLD (compose-time) partition is deleted, not left dangling.
    assert fake.articles_deletes == [("backlog", compose_time.year, compose_time, article_id)]

    # Root-caused live 2026-08-10 (Bing Webmaster Tools submitted-URL audit):
    # an admin-approved article's row never carried its slug forward at
    # release at all, so IndexNow and the homepage fell back to the raw
    # uuid for every such article. The slug must be carried on release, and
    # threaded through to the IndexNow ping.
    assert len(fake.slug_updates) == 1
    assert fake.slug_updates[0][0] == "a-real-slug"
    assert fake.slug_updates[0][-1] == article_id
    assert pinged[0]["kwargs"]["slug"] == "a-real-slug"


def test_publish_article_to_feed_skips_slug_write_when_article_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No slug write at all for an article that never claimed one — nothing to carry, and no accidental empty-string slug landing in `articles`."""
    article_id = uuid4()
    row = _article_row(
        article_id, published_at=datetime.now(tz=UTC) - timedelta(hours=5), slug=None
    )
    fake = _FakeSession(row)
    _patch(monkeypatch, fake)
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", lambda *_a, **_kw: None)

    store = AdminCassandraStore()
    assert store._publish_article_to_feed(str(article_id)) is True
    assert fake.slug_updates == []
