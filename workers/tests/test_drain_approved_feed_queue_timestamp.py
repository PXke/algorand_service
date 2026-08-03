"""Releasing an approved backlog item stamps release time, not compose time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.modules.newspaper.tasks import queue_drain_tasks


class _PendingRow:
    def __init__(self, article_id: str) -> None:
        self.bucket = "main"
        self.interest_score = 1.0
        self.approved_at = datetime.now(tz=UTC)
        self.article_id = article_id


class _Result:
    def __init__(self, row: Any = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row/result
        self._row = row

    def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
        return self._row


class _FakeSession:
    """Mirrors the pattern in workers/tests/test_domain_status_sticky.py: prepare() returns the raw CQL so execute() can branch on query text."""

    def __init__(self, *, pending_rows: list[_PendingRow], article_row: Any) -> None:  # noqa: ANN401 -- duck-typed Cassandra row/result
        self._pending_rows = pending_rows
        self._article_row = article_row
        self.feed_inserts: list[tuple] = []
        self.feed_deletes: list[tuple] = []
        self.published_at_updates: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "pending_feed_queue" in q:
            return list(self._pending_rows)
        if q.startswith("SELECT") and "articles_by_id" in q:
            return _Result(self._article_row)
        if q.startswith("INSERT INTO algorand_platform.articles_feed"):
            self.feed_inserts.append(tuple(params))
        elif q.startswith("DELETE FROM algorand_platform.articles_feed"):
            self.feed_deletes.append(tuple(params))
        elif q.startswith("UPDATE algorand_platform.articles_by_id SET published_at"):
            self.published_at_updates.append(tuple(params))
        return _Result(None)


def test_drain_approved_feed_queue_stamps_release_time_and_keeps_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same staleness bug as the backend release path: a held article's published_at was stamped at compose time — releasing it from pending_feed_queue must stamp the real release moment, and (a second, independent bug found alongside it) must not drop image_url/source_url the way the old INSERT_BASIC statement did."""
    article_id = uuid4()
    compose_time = datetime.now(tz=UTC) - timedelta(hours=5)
    article_row = SimpleNamespace(
        article_id=article_id,
        service_id="svc",
        title="Title",
        summary="Summary",
        published_at=compose_time,
        tags=["a", "b"],
        image_url="https://example.com/img.png",
        source_url="https://example.com/",
    )
    fake = _FakeSession(pending_rows=[_PendingRow(article_id)], article_row=article_row)

    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()

    monkeypatch.setattr(
        "app.modules.newspaper.publish_policy.remaining_standard_publish_slots",
        lambda: 3,
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_schedule.is_standard_publish_due",
        lambda: (True, "no_prior_standard_publish"),
    )
    monkeypatch.setattr(queue_drain_tasks, "record_standard_publish", lambda: None)
    # Backlog releases reserve their slot in the daily-cap counter like any
    # other standard publish (2026-07-18) — stub the Redis-backed guard.
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.reserve_publish_slot",
        lambda **_kw: (True, "ok"),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.publish_tasks.enqueue_article_translations",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr("app.modules.newspaper.indexnow.ping_article", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.distribution_tasks.distribute_article",
        SimpleNamespace(delay=lambda *_a, **_kw: None),
    )

    before = datetime.now(tz=UTC)
    result = queue_drain_tasks.drain_approved_feed_queue()
    after = datetime.now(tz=UTC)

    assert result["status"] == "ok"
    assert result["published"] == 1

    assert len(fake.feed_inserts) == 1
    feed_params = fake.feed_inserts[0]
    feed_published_at = feed_params[1]
    assert feed_published_at != compose_time
    assert before <= feed_published_at <= after
    # bucket, published_at, article_id, service_id, title, summary, tags, image_url, source_url
    assert feed_params[7] == "https://example.com/img.png"
    assert feed_params[8] == "https://example.com/"

    assert len(fake.published_at_updates) == 1
    assert fake.published_at_updates[0] == (feed_published_at, article_id)


def test_release_deletes_any_pre_existing_feed_row_before_inserting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A defensive cleanup: if this article somehow already had a feed row at its pre-release (compose-time) published_at, releasing it must delete that row first -- otherwise the article ends up with two live feed rows (observed 2026-08-03: a forced/manual drain released an article that, unexpectedly, was already fed)."""
    from app.core.feed_bucket import feed_month

    article_id = uuid4()
    compose_time = datetime.now(tz=UTC) - timedelta(hours=5)
    article_row = SimpleNamespace(
        article_id=article_id,
        service_id="svc",
        title="Title",
        summary="Summary",
        published_at=compose_time,
        tags=["a", "b"],
        image_url="",
        source_url="https://example.com/",
    )
    fake = _FakeSession(pending_rows=[_PendingRow(article_id)], article_row=article_row)

    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()

    monkeypatch.setattr(
        "app.modules.newspaper.publish_policy.remaining_standard_publish_slots",
        lambda: 3,
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_schedule.is_standard_publish_due",
        lambda: (True, "no_prior_standard_publish"),
    )
    monkeypatch.setattr(queue_drain_tasks, "record_standard_publish", lambda: None)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.reserve_publish_slot",
        lambda **_kw: (True, "ok"),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.publish_tasks.enqueue_article_translations",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr("app.modules.newspaper.indexnow.ping_article", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.distribution_tasks.distribute_article",
        SimpleNamespace(delay=lambda *_a, **_kw: None),
    )

    result = queue_drain_tasks.drain_approved_feed_queue()

    assert result["status"] == "ok"
    assert len(fake.feed_deletes) == 1
    assert fake.feed_deletes[0] == (feed_month(compose_time), compose_time, article_id)
    # The delete must run before the insert, not after -- otherwise it would
    # wipe out the freshly-released row instead of the stale one.
    assert fake.feed_deletes[0][1] != fake.feed_inserts[0][1]
