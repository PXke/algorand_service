"""Publishing to the feed must stamp release time, not compose time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore

# `articles`' column order (see algorand_shared.article_transitions._ARTICLES_COLUMNS).
_ARTICLES_COLUMNS = (
    "status", "year", "published_at", "article_id", "service_id", "title", "summary", "body",
    "image_url", "tags", "source_url", "trigger_txid", "trigger_round", "slug", "translations",
    "first_published_at", "updated_at", "prompt_version", "composed_by_model",
    "deleted_at", "status_updated_at", "interest_score", "approved_at", "views",
)  # fmt: skip


class _Result:
    def __init__(self, row: Any = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._row = row

    def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row
        return self._row


class _FakeSession:
    """The statement registry resolves *Stmts.* by calling get_cassandra_session().prepare(cql); return the CQL text so execute() can branch on it (SELECT vs INSERT vs UPDATE), matching the pattern already used in workers/tests/test_domain_status_sticky.py."""

    def __init__(
        self,
        article_row: Any,  # noqa: ANN401 -- duck-typed Cassandra row
        *,
        service_id_rows: list | None = None,
    ) -> None:
        self._article_row = article_row
        # Rows FIND_BY_SERVICE_ID would return -- the conflict check Article.publish()
        # runs before every transition. Empty by default (no conflict).
        self._service_id_rows = service_id_rows if service_id_rows is not None else []
        self.articles_inserts: list[tuple] = []
        self.articles_deletes: list[tuple] = []
        self.slug_updates: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "FROM algorand_platform.articles WHERE article_id = ?" in q:
            return _Result(self._article_row)
        if q.startswith("SELECT") and "FROM algorand_platform.articles WHERE service_id = ?" in q:
            return self._service_id_rows
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
    assert values["slug"] == "a-real-slug"
    # The row's OLD (compose-time) partition is deleted, not left dangling.
    assert fake.articles_deletes == [("backlog", compose_time.year, compose_time, article_id)]

    # 2026-08-27: ensure_article_slug no longer issues a separate UPDATE for
    # an already-set slug (no claim needed, see its own docstring) -- the
    # value is carried forward by the transition's own full-row INSERT
    # (asserted above), so no slug_updates call is expected here.
    assert fake.slug_updates == []


def test_publish_article_to_feed_no_longer_reimplements_search_indexnow_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W4-A (2026-08-28): _publish_article_to_feed used to do its own Typesense upsert_article_document + IndexNow ping_article inline -- a fourth divergent copy of workers' "an article just went live" fanout. That fanout now goes through the shared app.tasks.newspaper.fanout_after_publish task (see _trigger_fanout_after_publish, exercised by test_publish_or_queue_article_triggers_shared_fanout_task below); _publish_article_to_feed itself must do ONLY the status transition."""
    article_id = uuid4()
    row = _article_row(
        article_id, published_at=datetime.now(tz=UTC) - timedelta(hours=5), slug="a-real-slug"
    )
    fake = _FakeSession(row)
    _patch(monkeypatch, fake)
    ping_mock = MagicMock()
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", ping_mock)
    typesense_mock = MagicMock()
    monkeypatch.setattr("app.core.typesense_client.upsert_article_document", typesense_mock)

    assert AdminCassandraStore()._publish_article_to_feed(str(article_id)) is True

    ping_mock.assert_not_called()
    typesense_mock.assert_not_called()


def test_publish_article_to_feed_claims_a_slug_when_the_draft_never_had_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held/review draft going live for the first time must CLAIM a slug, not silently skip.

    Root-caused live 2026-08-27 (Al Goanna recompose): a review/held draft
    never has a slug claimed for it -- slugs are only ever claimed at
    PUBLISH time, and this review-approval transition IS an article's
    first publish. This function used to only ever COPY an EXISTING slug
    forward across the release re-stamp; when there wasn't one yet (every
    review-approved article, always), it silently published with
    slug=NULL, falling back to a bare-UUID URL search engines never index
    cleanly.
    """
    article_id = uuid4()
    row = _article_row(
        article_id, published_at=datetime.now(tz=UTC) - timedelta(hours=5), slug=None
    )
    row.title = "A Fresh Headline"
    fake = _FakeSession(row)
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    assert store._publish_article_to_feed(str(article_id)) is True

    assert len(fake.slug_updates) == 1
    assert fake.slug_updates[0][0] == "a-fresh-headline"


def test_publish_article_to_feed_preserves_an_existing_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draft that already has a slug (the ordinary re-stamp case) is unaffected by the fix -- ensure_article_slug's existing-slug check is a plain read, no new claim attempted, and (2026-08-27) no redundant write either: the value is carried forward by the transition's own full-row INSERT."""
    article_id = uuid4()
    row = _article_row(
        article_id, published_at=datetime.now(tz=UTC) - timedelta(hours=5), slug="already-set"
    )
    fake = _FakeSession(row)
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    assert store._publish_article_to_feed(str(article_id)) is True
    values = dict(zip(_ARTICLES_COLUMNS, fake.articles_inserts[0], strict=True))
    assert values["slug"] == "already-set"
    assert fake.slug_updates == []


def test_publish_article_to_feed_refuses_a_duplicate_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual point of the 2026-08-27 Article.publish() migration.

    A DIFFERENT article_id already published for this service_id must abort
    the transition entirely, not silently create a duplicate (the exact
    shape of the HesabPay/AlgoRank/Al Goanna incidents).
    """
    from algorand_shared.article import DuplicateArticleError

    article_id = uuid4()
    existing_id = uuid4()
    row = _article_row(
        article_id, published_at=datetime.now(tz=UTC) - timedelta(hours=5), slug=None
    )
    fake = _FakeSession(
        row,
        service_id_rows=[SimpleNamespace(article_id=existing_id, status="published")],
    )
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    with pytest.raises(DuplicateArticleError):
        store._publish_article_to_feed(str(article_id))

    # No transition was attempted -- the old row must not move at all.
    assert fake.articles_inserts == []
    assert fake.articles_deletes == []


def test_trigger_fanout_after_publish_sends_the_shared_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W4-A (2026-08-28): backend dispatches the post-publish fanout (search index, translations, IndexNow, distribution) via workers' shared app.tasks.newspaper.fanout_after_publish task, by name over the Celery broker -- not its own reimplementation."""
    sent: dict[str, Any] = {}

    class _FakeCelery:
        def __init__(self, broker: str) -> None:
            pass

        def send_task(self, name: str, *, args: list, kwargs: dict, queue: str) -> None:
            sent["name"] = name
            sent["args"] = args
            sent["kwargs"] = kwargs
            sent["queue"] = queue

    monkeypatch.setattr("celery.Celery", _FakeCelery)

    AdminCassandraStore._trigger_fanout_after_publish("11111111-1111-1111-1111-111111111111")

    assert sent["name"] == "app.tasks.newspaper.fanout_after_publish"
    assert sent["args"] == ["11111111-1111-1111-1111-111111111111"]
    assert sent["kwargs"] == {"distribute": True}
    assert sent["queue"] == "pipeline"


def test_trigger_fanout_after_publish_swallows_broker_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A broker hiccup dispatching the fanout must be logged, not raised -- the article is already durably published by the time this fires."""
    import logging

    class _BoomCelery:
        def __init__(self, broker: str) -> None:
            pass

        def send_task(self, *_a: object, **_kw: object) -> None:
            raise ConnectionError("broker unreachable")

    monkeypatch.setattr("celery.Celery", _BoomCelery)

    with caplog.at_level(logging.WARNING):
        AdminCassandraStore._trigger_fanout_after_publish("aid")  # must not raise

    assert any("failed to trigger fanout_after_publish" in rec.message for rec in caplog.records)


def test_publish_or_queue_article_triggers_fanout_only_when_actually_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: _publish_or_queue_article must call _trigger_fanout_after_publish exactly when _publish_article_to_feed actually landed the article live, and never when the daily cap routes it to backlog instead."""
    store = AdminCassandraStore()
    monkeypatch.setattr(store, "_feed_count_today", lambda *_a, **_kw: 0)
    monkeypatch.setattr(store, "_is_standard_publish_due", lambda: True)
    monkeypatch.setattr(store, "_record_standard_publish", lambda: None)
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: SimpleNamespace())
    fanout_calls: list[str] = []
    monkeypatch.setattr(
        AdminCassandraStore, "_trigger_fanout_after_publish", staticmethod(fanout_calls.append)
    )

    monkeypatch.setattr(store, "_publish_article_to_feed", lambda _aid: True)
    result = store._publish_or_queue_article("aid-1")
    assert result == "published"
    assert fanout_calls == ["aid-1"]

    fanout_calls.clear()
    monkeypatch.setattr(store, "_publish_article_to_feed", lambda _aid: False)
    result = store._publish_or_queue_article("aid-2")
    assert result == "published"
    assert fanout_calls == []
