"""URL normalization and the crawl-frontier enqueue/dequeue queue."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.crawler.url_queue import _normalize_url, dequeue_url, enqueue_url, mark_url_done


def test_normalize_url_adds_scheme() -> None:
    """Adds an https:// scheme to a bare URL."""
    assert _normalize_url("example.com/path") == "https://example.com/path"


def test_normalize_url_strips_www() -> None:
    """Collapses www./bare/mixed-case host variants of the same URL to one normalized form."""
    # www./bare variants of the same homepage must collapse to one cooldown
    # key, or the recrawl cooldown can't stop the same site being hit twice
    # within minutes (root-caused 2026-07-21: quantoz.com).
    assert _normalize_url("https://www.example.com") == _normalize_url("https://example.com")
    assert _normalize_url("https://WWW.Example.com/Path") == "https://example.com/Path"


def test_enqueue_url_deduplicates_pending(
    monkeypatch: pytest.MonkeyPatch, fake_cassandra_session: MagicMock
) -> None:
    """Returns the existing pending queue row instead of inserting a duplicate."""
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True)
    existing = MagicMock(queue_id="existing-id")
    fake_cassandra_session.execute.side_effect = [
        MagicMock(one=MagicMock(return_value=existing)),
        MagicMock(one=MagicMock(return_value=MagicMock(status="pending"))),
    ]
    queue_id, created = enqueue_url("https://example.com", source="test", priority=10)
    assert created is False
    assert queue_id == "existing-id"


def test_enqueue_url_inserts_new(
    monkeypatch: pytest.MonkeyPatch, fake_cassandra_session: MagicMock
) -> None:
    """Inserts a brand-new URL when no pending row exists for it yet."""
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True)
    fake_cassandra_session.execute.return_value = MagicMock(one=MagicMock(return_value=None))
    queue_id, created = enqueue_url("https://new.example.com", source="chain", priority=50)
    assert created is True
    assert queue_id
    assert fake_cassandra_session.execute.call_count >= 3


def test_dequeue_url_returns_highest_priority_item(
    monkeypatch: pytest.MonkeyPatch, fake_cassandra_session: MagicMock
) -> None:
    """Dequeues an item from the random-pick pool of pending rows with its priority and metadata intact."""
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True)
    pending = MagicMock(
        queue_id="q-high",
        url="https://high.example.com",
        source="chain",
        priority=50,
        enqueued_at=MagicMock(),
    )
    meta_row = MagicMock(metadata={"service_id": "svc-1"})
    # dequeue_url() now picks randomly among the top URL_QUEUE_RANDOM_PICK_POOL
    # rows (PEEK_PENDING_BATCH, a plain iterable) rather than always the single
    # front row (PEEK_PENDING, .one()) — a single-candidate batch keeps
    # random.choice's result deterministic for this test.
    fake_cassandra_session.execute.side_effect = [
        [pending],
        MagicMock(),
        MagicMock(),
        MagicMock(one=MagicMock(return_value=meta_row)),
    ]
    item = dequeue_url()
    assert item is not None
    assert item["url"] == "https://high.example.com"
    assert item["priority"] == 50
    assert item["metadata"]["service_id"] == "svc-1"


def test_dequeue_url_pool_of_one_uses_front_row(
    monkeypatch: pytest.MonkeyPatch, fake_cassandra_session: MagicMock
) -> None:
    """With URL_QUEUE_RANDOM_PICK_POOL=1, dequeue always returns the strict front-of-queue row."""
    # URL_QUEUE_RANDOM_PICK_POOL=1 restores the old strictly-front-of-queue
    # behavior (PEEK_PENDING, LIMIT 1, .one()).
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True)
    monkeypatch.setattr("app.core.config.URL_QUEUE_RANDOM_PICK_POOL", 1)
    pending = MagicMock(
        queue_id="q-only",
        url="https://only.example.com",
        source="chain",
        priority=50,
        enqueued_at=MagicMock(),
    )
    meta_row = MagicMock(metadata={})
    fake_cassandra_session.execute.side_effect = [
        MagicMock(one=MagicMock(return_value=pending)),
        MagicMock(),
        MagicMock(),
        MagicMock(one=MagicMock(return_value=meta_row)),
    ]
    item = dequeue_url()
    assert item is not None
    assert item["url"] == "https://only.example.com"


def test_dequeue_url_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None immediately when the URL queue feature flag is off."""
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", False)
    assert dequeue_url() is None


def test_mark_url_done_updates_status(fake_cassandra_session: MagicMock) -> None:
    """Marking a queue item done issues exactly one Cassandra status-update write."""
    mark_url_done("00000000-0000-4000-8000-000000000001", status="skipped")
    fake_cassandra_session.execute.assert_called_once()
