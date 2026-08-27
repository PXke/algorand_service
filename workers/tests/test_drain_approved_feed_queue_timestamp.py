"""Releasing an approved backlog item stamps release time, not compose time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.modules.newspaper.tasks import queue_drain_tasks

# `articles`' column order (see algorand_shared.article_transitions._ARTICLES_COLUMNS).
_ARTICLES_COLUMNS = (
    "status", "year", "published_at", "article_id", "service_id", "title", "summary", "body",
    "image_url", "tags", "source_url", "trigger_txid", "trigger_round", "slug", "translations",
    "first_published_at", "updated_at", "prompt_version", "composed_by_model",
    "deleted_at", "status_updated_at", "interest_score", "approved_at",
)  # fmt: skip


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
        """Return the wrapped row (or None)."""
        return self._row


class _FakeSession:
    """Mirrors the pattern in workers/tests/test_domain_status_sticky.py: prepare() returns the raw CQL so execute() can branch on query text."""

    def __init__(
        self,
        *,
        pending_rows: list[_PendingRow],
        article_row: Any,  # noqa: ANN401 -- duck-typed Cassandra row/result
        service_id_rows: list | None = None,
    ) -> None:
        self._pending_rows = pending_rows
        self._article_row = article_row
        # Rows FIND_BY_SERVICE_ID would return -- the conflict check
        # Article.publish() runs before every transition. Empty by default.
        self._service_id_rows = service_id_rows if service_id_rows is not None else []
        self.articles_inserts: list[tuple] = []
        self.articles_deletes: list[tuple] = []

    def prepare(self, cql: str) -> str:
        """Identity passthrough -- lets execute() branch on the raw CQL text."""
        return cql

    def execute(self, query: str, params: tuple = ()) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "FROM algorand_platform.articles WHERE service_id = ?" in q:
            return self._service_id_rows
        if q.startswith("SELECT") and "status = 'backlog'" in q:
            # list_backlog_articles() -- the pending_feed_queue rows re-shaped
            # as the `articles` status='backlog' rows it now reads instead.
            return [
                SimpleNamespace(
                    article_id=row.article_id,
                    service_id=getattr(self._article_row, "service_id", ""),
                    title=getattr(self._article_row, "title", ""),
                    interest_score=row.interest_score,
                    approved_at=row.approved_at,
                )
                for row in self._pending_rows
            ]
        if q.startswith("SELECT") and "pending_feed_queue" in q:
            return list(self._pending_rows)
        if q.startswith("SELECT") and "FROM algorand_platform.articles WHERE article_id = ?" in q:
            # Both the top-level GET_FULL_BY_ID read and transition_article_
            # status's own internal re-read share this exact query text.
            return _Result(self._article_row)
        if q.startswith("INSERT INTO algorand_platform.articles ("):
            self.articles_inserts.append(tuple(params))
        elif q.startswith("DELETE FROM algorand_platform.articles "):
            self.articles_deletes.append(tuple(params))
        return _Result(None)


def _article_row(
    article_id: object, *, status: str = "backlog", published_at: datetime
) -> SimpleNamespace:
    values: dict[str, object] = dict.fromkeys(_ARTICLES_COLUMNS)
    values.update(
        status=status,
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
    )
    return SimpleNamespace(**values)


def _patch_common(monkeypatch: pytest.MonkeyPatch, fake: _FakeSession) -> None:
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


def test_drain_approved_feed_queue_stamps_release_time_and_keeps_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held article's published_at was stamped at compose time — releasing it must re-stamp the real release moment on the `articles` row, carrying image_url/source_url forward unchanged (transition_article_status preserves every column not explicitly overridden)."""
    article_id = uuid4()
    compose_time = datetime.now(tz=UTC) - timedelta(hours=5)
    fake = _FakeSession(
        pending_rows=[_PendingRow(article_id)],
        article_row=_article_row(article_id, published_at=compose_time),
    )
    _patch_common(monkeypatch, fake)

    before = datetime.now(tz=UTC)
    result = queue_drain_tasks.drain_approved_feed_queue()
    after = datetime.now(tz=UTC)

    assert result["status"] == "ok"
    assert result["published"] == 1

    assert len(fake.articles_inserts) == 1
    values = dict(zip(_ARTICLES_COLUMNS, fake.articles_inserts[0], strict=True))
    assert values["status"] == "published"
    assert values["published_at"] != compose_time
    assert before <= values["published_at"] <= after
    assert values["image_url"] == "https://example.com/img.png"
    assert values["source_url"] == "https://example.com/"


def test_release_deletes_the_pre_release_partition_before_inserting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """transition_article_status deletes the row's OLD (pre-release, compose-time) partition before inserting the new one -- published_at is part of the partition key, so this is what keeps a released article from ending up with two live `articles` rows (the old bug, pre-consolidation: two live feed rows for one article)."""
    article_id = uuid4()
    compose_time = datetime.now(tz=UTC) - timedelta(hours=5)
    fake = _FakeSession(
        pending_rows=[_PendingRow(article_id)],
        article_row=_article_row(article_id, published_at=compose_time),
    )
    _patch_common(monkeypatch, fake)

    result = queue_drain_tasks.drain_approved_feed_queue()

    assert result["status"] == "ok"
    assert len(fake.articles_deletes) == 1
    assert fake.articles_deletes[0] == ("backlog", compose_time.year, compose_time, article_id)
    # The delete must target the OLD (compose-time) partition, not the
    # freshly-stamped release one.
    assert len(fake.articles_inserts) == 1
    new_published_at = dict(zip(_ARTICLES_COLUMNS, fake.articles_inserts[0], strict=True))[
        "published_at"
    ]
    assert new_published_at != compose_time


def test_backlog_release_refuses_a_duplicate_service_and_returns_the_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-08-27 Article.publish() migration.

    A backlog-held article must not be released if a DIFFERENT article_id
    already went live for the same service_id while it waited -- the slot
    is handed back (not burned) and the run ends cleanly, no crash.
    """
    article_id = uuid4()
    existing_id = uuid4()
    compose_time = datetime.now(tz=UTC) - timedelta(hours=5)
    fake = _FakeSession(
        pending_rows=[_PendingRow(article_id)],
        article_row=_article_row(article_id, published_at=compose_time),
        service_id_rows=[SimpleNamespace(article_id=existing_id, status="published")],
    )
    _patch_common(monkeypatch, fake)
    released_slots: list[str] = []
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.release_publish_slot",
        lambda **_kw: released_slots.append("released"),
    )

    result = queue_drain_tasks.drain_approved_feed_queue()

    assert result["status"] == "ok"
    assert result["published"] == 0
    assert fake.articles_inserts == []
    assert fake.articles_deletes == []
    assert released_slots == ["released"]
