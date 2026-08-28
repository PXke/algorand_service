"""Releasing an approved backlog item stamps release time, not compose time."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.modules.newspaper import publish_fanout
from app.modules.newspaper.tasks import queue_drain_tasks

# `articles`' column order (see algorand_shared.article_transitions._ARTICLES_COLUMNS).
_ARTICLES_COLUMNS = (
    "status", "year", "published_at", "article_id", "service_id", "title", "summary", "body",
    "image_url", "tags", "source_url", "trigger_txid", "trigger_round", "slug", "translations",
    "translated_titles", "first_published_at", "updated_at", "prompt_version", "composed_by_model",
    "deleted_at", "status_updated_at", "interest_score", "approved_at", "views",
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
    # enqueue_article_translations stays a genuine function-local import in
    # fanout_after_publish (circular import: publish_tasks.py imports
    # publish_fanout.py) -- patched at its origin module. ping_article/
    # distribute_article are module-top imports in publish_fanout.py (no
    # circular-import forces them local, CLAUDE.md Sec.3), so they're
    # patched on publish_fanout's own bound name instead.
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.publish_tasks.enqueue_article_translations",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(publish_fanout, "ping_article", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        publish_fanout, "distribute_article", SimpleNamespace(delay=lambda *_a, **_kw: None)
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


def _backlog_row(article_id: object, *, interest_score: float) -> SimpleNamespace:
    """Shape LIST_BACKLOG's own SELECT returns -- article_id/service_id/title/interest_score/approved_at only, see ArticlesStmts.LIST_BACKLOG."""
    return SimpleNamespace(
        article_id=article_id,
        service_id="",
        title="",
        interest_score=interest_score,
        approved_at=datetime.now(tz=UTC),
    )


class _FlakyByIdSession:
    """Two DIFFERENT backlog article_ids: a dead one and a live one.

    The by-id read for the dead one misses once then succeeds -- the
    transient-consistency case W2-A(1)'s fix targets.
    """

    def __init__(self, *, missing_id: object, real_id: object, real_row: object) -> None:
        self._missing_id = missing_id
        self._real_id = real_id
        self._real_row = real_row
        self.articles_inserts: list[tuple] = []
        self.articles_deletes: list[tuple] = []
        self._missing_by_id_calls = 0

    def prepare(self, cql: str) -> str:
        return cql

    def _backlog_rows(self) -> list[SimpleNamespace]:
        discarded = {row[3] for row in self.articles_inserts if row[0] != "backlog"}
        return [
            _backlog_row(aid, interest_score=score)
            for aid, score in ((self._missing_id, 99.0), (self._real_id, 1.0))
            if aid not in discarded
        ]

    def _by_id(self, article_id: object) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
        if article_id == self._real_id:
            return _Result(self._real_row)
        if article_id == self._missing_id:
            self._missing_by_id_calls += 1
            # First read (the one `_release_pending_feed_backlog` itself
            # does) transiently misses; the retry inside
            # transition_article_status finds it.
            if self._missing_by_id_calls == 1:
                return _Result(None)
            return _Result(_article_row(self._missing_id, published_at=self._real_row.published_at))
        return _Result(None)

    def execute(self, query: str, params: tuple = ()) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "WHERE service_id = ?" in q:
            return []
        if q.startswith("SELECT") and "status = 'backlog'" in q:
            return self._backlog_rows()
        if q.startswith("SELECT") and "WHERE article_id = ?" in q:
            (aid,) = params
            return self._by_id(aid)
        if q.startswith("INSERT INTO algorand_platform.articles ("):
            self.articles_inserts.append(tuple(params))
        elif q.startswith("DELETE FROM algorand_platform.articles "):
            self.articles_deletes.append(tuple(params))
        return _Result(None)


def test_missing_article_row_is_transitioned_so_a_later_run_reaches_the_next_row(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """W2-A(1): a transient by-id miss on a dead backlog row must self-heal.

    The by-id read `_release_pending_feed_backlog` uses to fetch the row it's
    about to release can transiently miss a row `list_backlog_articles`'s own
    (separate) partition scan just found -- e.g. a coordinator hitting a
    differently-lagging replica on the second, unrelated read. Previously
    this only logged and fell through, and since ``rows = backlog[:1]``
    always re-selects the SAME (highest priority, never-changing) top row, a
    persistently-missed row would jam every future run on the identical dead
    row forever. The row must now be moved to a terminal status via the
    shared `transition_article_status` helper (which performs its own fresh
    by-id read) so it drops out of the next `status='backlog'` scan and a
    later run reaches the next-best candidate.
    """
    missing_id = uuid4()
    real_id = uuid4()
    compose_time = datetime.now(tz=UTC) - timedelta(hours=5)
    real_row = _article_row(real_id, published_at=compose_time)
    fake = _FlakyByIdSession(missing_id=missing_id, real_id=real_id, real_row=real_row)
    _patch_common(monkeypatch, fake)

    with caplog.at_level(logging.WARNING):
        first = queue_drain_tasks._release_pending_feed_backlog(slots=1)

    assert first["published"] == 0
    assert any("marked discarded_missing" in rec.message for rec in caplog.records)
    # The dead row's own status transitioned to the terminal one instead of
    # being left dangling as 'backlog' forever.
    assert any(
        row[0] == "discarded_missing" and row[3] == missing_id for row in fake.articles_inserts
    )

    # A later run now reaches the next-best backlog candidate instead of
    # retrying the identical dead row.
    second = queue_drain_tasks._release_pending_feed_backlog(slots=1)
    assert second["published"] == 1


class _AlwaysMissingSession:
    """A backlog row whose by-id read misses on every attempt.

    A persistently, not just transiently, missing article record.
    """

    def __init__(self, *, missing_id: object) -> None:
        self._missing_id = missing_id

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, _params: tuple = ()) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "WHERE service_id = ?" in q:
            return []
        if q.startswith("SELECT") and "status = 'backlog'" in q:
            return [_backlog_row(self._missing_id, interest_score=99.0)]
        return _Result(None)


def test_missing_article_row_still_reports_stuck_when_transition_also_misses(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A permanently missing by-id read is a no-op, but must not be silent.

    `transition_article_status`'s own internal read shares the same miss and
    is a no-op -- the row stays 'backlog'. The warning has to say so plainly
    so the stuck row is at least visible/alertable, matching this
    codebase's fail-open-with-a-log-line posture elsewhere.
    """
    missing_id = uuid4()
    fake = _AlwaysMissingSession(missing_id=missing_id)
    _patch_common(monkeypatch, fake)

    with caplog.at_level(logging.WARNING):
        result = queue_drain_tasks._release_pending_feed_backlog(slots=1)

    assert result["published"] == 0
    assert any(
        "transition_article_status ALSO found no row; still stuck" in rec.message
        for rec in caplog.records
    )


def test_indexnow_ping_failure_is_logged_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """W2-A(5): an IndexNow ping failure must be logged, not swallowed.

    A failed best-effort IndexNow ping on a backlog release must be logged
    (with the traceback), not a bare `except Exception: pass` -- it must
    also never fail the release itself.
    """
    article_id = uuid4()
    compose_time = datetime.now(tz=UTC) - timedelta(hours=5)
    fake = _FakeSession(
        pending_rows=[_PendingRow(article_id)],
        article_row=_article_row(article_id, published_at=compose_time),
    )
    _patch_common(monkeypatch, fake)

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("indexnow unreachable")

    monkeypatch.setattr(publish_fanout, "ping_article", _boom)

    with caplog.at_level(logging.WARNING):
        result = queue_drain_tasks.drain_approved_feed_queue()

    assert result["status"] == "ok"
    assert result["published"] == 1
    matches = [rec for rec in caplog.records if "IndexNow ping failed" in rec.message]
    assert matches
    assert matches[0].exc_info is not None


def test_backlog_release_now_indexes_the_article_into_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W4-A regression: _release_pending_feed_backlog used to reimplement the direct-publish path's fanout by hand and never called index_article at all, so a released article silently never entered Typesense until the once-daily reindex_articles safety net caught it. It now goes through the shared fanout_after_publish, which does index it."""
    article_id = uuid4()
    compose_time = datetime.now(tz=UTC) - timedelta(hours=5)
    fake = _FakeSession(
        pending_rows=[_PendingRow(article_id)],
        article_row=_article_row(article_id, published_at=compose_time),
    )
    _patch_common(monkeypatch, fake)
    index_mock = MagicMock()
    monkeypatch.setattr(publish_fanout, "index_article", index_mock)

    result = queue_drain_tasks.drain_approved_feed_queue()

    assert result["status"] == "ok"
    assert result["published"] == 1
    index_mock.delay.assert_called_once()
    _, kwargs = index_mock.delay.call_args
    assert kwargs["article_id"] == str(article_id)
    assert kwargs["service_id"] == "svc"
    assert kwargs["title"] == "Title"


def test_backlog_release_goes_through_shared_fanout_after_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: _release_pending_feed_backlog must call the shared fanout_after_publish (W4-A) instead of reimplementing its own copy of the post-publish steps."""
    article_id = uuid4()
    compose_time = datetime.now(tz=UTC) - timedelta(hours=5)
    fake = _FakeSession(
        pending_rows=[_PendingRow(article_id)],
        article_row=_article_row(article_id, published_at=compose_time),
    )
    _patch_common(monkeypatch, fake)
    fanout_mock = MagicMock(return_value={"status": "ok"})
    monkeypatch.setattr(queue_drain_tasks, "fanout_after_publish", fanout_mock)

    result = queue_drain_tasks.drain_approved_feed_queue()

    assert result["status"] == "ok"
    assert result["published"] == 1
    fanout_mock.assert_called_once_with(str(article_id), distribute=True)
