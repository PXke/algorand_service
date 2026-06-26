from __future__ import annotations

from unittest.mock import MagicMock

from app.modules.crawler.url_queue import _normalize_url, dequeue_url, enqueue_url, mark_url_done


def test_normalize_url_adds_scheme() -> None:
    assert _normalize_url("example.com/path") == "https://example.com/path"


def test_enqueue_url_deduplicates_pending(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True)
    session = MagicMock()
    existing = MagicMock(queue_id="existing-id")
    session.execute.side_effect = [
        MagicMock(one=MagicMock(return_value=existing)),
        MagicMock(one=MagicMock(return_value=MagicMock(status="pending"))),
    ]
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)
    queue_id, created = enqueue_url("https://example.com", source="test", priority=10)
    assert created is False
    assert queue_id == "existing-id"


def test_enqueue_url_inserts_new(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True)
    session = MagicMock()
    session.execute.return_value = MagicMock(one=MagicMock(return_value=None))
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)
    queue_id, created = enqueue_url("https://new.example.com", source="chain", priority=50)
    assert created is True
    assert queue_id
    assert session.execute.call_count >= 3


def test_dequeue_url_returns_highest_priority_item(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True)
    session = MagicMock()
    pending = MagicMock(
        queue_id="q-high",
        url="https://high.example.com",
        source="chain",
        priority=50,
        enqueued_at=MagicMock(),
    )
    meta_row = MagicMock(metadata={"service_id": "svc-1"})
    session.execute.side_effect = [
        MagicMock(one=MagicMock(return_value=pending)),
        MagicMock(),
        MagicMock(),
        MagicMock(one=MagicMock(return_value=meta_row)),
    ]
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)
    item = dequeue_url()
    assert item is not None
    assert item["url"] == "https://high.example.com"
    assert item["priority"] == 50
    assert item["metadata"]["service_id"] == "svc-1"


def test_dequeue_url_disabled(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", False)
    assert dequeue_url() is None


def test_mark_url_done_updates_status(monkeypatch) -> None:
    session = MagicMock()
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)
    mark_url_done("00000000-0000-4000-8000-000000000001", status="skipped")
    session.execute.assert_called_once()
