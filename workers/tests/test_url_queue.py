"""URL normalization and the crawl-frontier enqueue/dequeue queue."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from conftest import FakeRedis

from app.modules.crawler.url_queue import (
    _normalize_url,
    dequeue_url,
    enqueue_url,
    mark_url_done,
    reclaim_stale_processing_urls,
)


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


def test_enqueue_url_binds_configured_row_ttl(
    monkeypatch: pytest.MonkeyPatch, fake_cassandra_session: MagicMock
) -> None:
    """All three enqueue inserts bind URL_QUEUE_ROW_TTL_SECONDS as their trailing USING TTL param."""
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True)
    monkeypatch.setattr("app.core.config.URL_QUEUE_ROW_TTL_SECONDS", 2_592_000)
    fake_cassandra_session.execute.return_value = MagicMock(one=MagicMock(return_value=None))
    _, created = enqueue_url("https://ttl.example.com", source="chain", priority=10)
    assert created is True
    # Call order: BY_URL dedupe lookup, then INSERT / INSERT_BY_URL /
    # INSERT_PENDING — the TTL is each write statement's last bind marker.
    inserts = fake_cassandra_session.execute.call_args_list[-3:]
    assert [call.args[1][-1] for call in inserts] == [2_592_000, 2_592_000, 2_592_000]


def test_enqueue_url_disabled_ttl_binds_zero(
    monkeypatch: pytest.MonkeyPatch, fake_cassandra_session: MagicMock
) -> None:
    """With the TTL knob at its 0 default, the writes bind TTL 0 — CQL's documented no-TTL value."""
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True)
    monkeypatch.setattr("app.core.config.URL_QUEUE_ROW_TTL_SECONDS", 0)
    fake_cassandra_session.execute.return_value = MagicMock(one=MagicMock(return_value=None))
    _, created = enqueue_url("https://nottl.example.com", source="chain", priority=10)
    assert created is True
    inserts = fake_cassandra_session.execute.call_args_list[-3:]
    assert [call.args[1][-1] for call in inserts] == [0, 0, 0]


def test_dequeue_url_status_update_binds_row_ttl(
    monkeypatch: pytest.MonkeyPatch, fake_cassandra_session: MagicMock
) -> None:
    """Dequeue's status='processing' UPDATE binds the same row TTL (leading USING TTL param)."""
    # TTLs are per-cell: a TTL-less status UPDATE would leave a cell outliving
    # the insert's cells, surfacing a phantom queue_id+status row later.
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True)
    monkeypatch.setattr("app.core.config.URL_QUEUE_ROW_TTL_SECONDS", 604_800)
    pending = MagicMock(
        queue_id="q-ttl",
        url="https://ttl.example.com",
        source="chain",
        priority=50,
        enqueued_at=MagicMock(),
    )
    fake_cassandra_session.execute.side_effect = [
        [pending],
        MagicMock(),
        MagicMock(),
        MagicMock(one=MagicMock(return_value=MagicMock(metadata={}))),
    ]
    assert dequeue_url() is not None
    update_call = fake_cassandra_session.execute.call_args_list[1]
    assert update_call.args[1] == (604_800, "processing", "q-ttl")


def test_mark_url_done_binds_row_ttl(
    monkeypatch: pytest.MonkeyPatch, fake_cassandra_session: MagicMock
) -> None:
    """mark_url_done's UPDATE binds (ttl, status, queue_id) in statement order."""
    monkeypatch.setattr("app.core.config.URL_QUEUE_ROW_TTL_SECONDS", 2_592_000)
    mark_url_done("00000000-0000-4000-8000-000000000001", status="done")
    params = fake_cassandra_session.execute.call_args.args[1]
    assert params[0] == 2_592_000
    assert params[1] == "done"
    assert str(params[2]) == "00000000-0000-4000-8000-000000000001"


# --------------------------------------------------------------------------- #
# processing-start markers + reclaim_stale_processing_urls (W3-A)
# --------------------------------------------------------------------------- #


def test_dequeue_url_records_processing_start_marker(
    monkeypatch: pytest.MonkeyPatch,
    fake_cassandra_session: MagicMock,
    patch_redis_from_url: FakeRedis,
) -> None:
    """A successful dequeue stamps a Redis processing-start marker, so reclaim_stale_processing_urls can find the row if its worker dies mid-fetch."""
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True)
    pending = MagicMock(
        queue_id="q-mark",
        url="https://mark.example.com",
        source="chain",
        priority=42,
        enqueued_at=MagicMock(),
    )
    fake_cassandra_session.execute.side_effect = [
        [pending],
        MagicMock(),
        MagicMock(),
        MagicMock(one=MagicMock(return_value=MagicMock(metadata={}))),
    ]
    assert dequeue_url() is not None
    marker = patch_redis_from_url.hashes["crawl:url_queue:processing"]["q-mark"]
    data = json.loads(marker)
    assert data["url"] == "https://mark.example.com"
    assert data["source"] == "chain"
    assert data["priority"] == 42
    assert "started_at" in data


def test_mark_url_done_clears_processing_marker(
    fake_cassandra_session: MagicMock,  # noqa: ARG001 -- fixture patches get_cassandra_session as a seam, not asserted on here
    patch_redis_from_url: FakeRedis,
) -> None:
    """Marking a queue item done removes its Redis processing-start marker."""
    qid = "00000000-0000-4000-8000-000000000001"
    patch_redis_from_url.hashes["crawl:url_queue:processing"] = {qid: "{}"}
    mark_url_done(qid, status="done")
    assert qid not in patch_redis_from_url.hashes.get("crawl:url_queue:processing", {})


def test_reclaim_stale_processing_urls_resets_stale_row(
    fake_cassandra_session: MagicMock, patch_redis_from_url: FakeRedis
) -> None:
    """Resets a row whose processing-start marker is older than the staleness threshold back to pending, re-inserting it into url_queue_pending with its original url/source/priority."""
    qid = "00000000-0000-4000-8000-0000000000aa"
    stale_started_at = datetime.now(tz=UTC).timestamp() - 3600  # 1h ago
    patch_redis_from_url.hashes["crawl:url_queue:processing"] = {
        qid: json.dumps(
            {
                "url": "https://stale.example.com",
                "source": "chain",
                "priority": 42,
                "started_at": stale_started_at,
            }
        )
    }
    fake_cassandra_session.execute.side_effect = [
        MagicMock(one=MagicMock(return_value=MagicMock(status="processing"))),  # GET_STATUS
        MagicMock(),  # UPDATE_STATUS
        MagicMock(),  # INSERT_PENDING
    ]
    out = reclaim_stale_processing_urls()
    assert out["reclaimed"] == 1
    assert out["reclaimed_ids"] == [qid]
    update_call = fake_cassandra_session.execute.call_args_list[1]
    assert update_call.args[1][1] == "pending"
    assert str(update_call.args[1][2]) == qid
    insert_call = fake_cassandra_session.execute.call_args_list[2]
    params = insert_call.args[1]
    assert params[0] == "pending"
    assert params[1] == 42
    assert str(params[3]) == qid
    assert params[4] == "https://stale.example.com"
    assert params[5] == "chain"
    assert qid not in patch_redis_from_url.hashes.get("crawl:url_queue:processing", {})


def test_reclaim_stale_processing_urls_skips_fresh_rows(
    fake_cassandra_session: MagicMock, patch_redis_from_url: FakeRedis
) -> None:
    """A row whose marker is within the staleness window is left alone -- no Cassandra touch, no reclaim."""
    qid = "00000000-0000-4000-8000-0000000000bb"
    fresh_started_at = datetime.now(tz=UTC).timestamp() - 5  # 5s ago
    patch_redis_from_url.hashes["crawl:url_queue:processing"] = {
        qid: json.dumps(
            {
                "url": "https://fresh.example.com",
                "source": "chain",
                "priority": 42,
                "started_at": fresh_started_at,
            }
        )
    }
    out = reclaim_stale_processing_urls()
    assert out["reclaimed"] == 0
    fake_cassandra_session.execute.assert_not_called()
    assert qid in patch_redis_from_url.hashes["crawl:url_queue:processing"]


def test_reclaim_stale_processing_urls_skips_already_finished_row(
    fake_cassandra_session: MagicMock, patch_redis_from_url: FakeRedis
) -> None:
    """A row whose marker is stale but whose live Cassandra status has already moved past 'processing' (mark_url_done ran, the HDEL itself failed) is never reset -- only its stray marker is cleared, so a race never resurrects an already-completed row."""
    qid = "00000000-0000-4000-8000-0000000000cc"
    stale_started_at = datetime.now(tz=UTC).timestamp() - 3600
    patch_redis_from_url.hashes["crawl:url_queue:processing"] = {
        qid: json.dumps(
            {
                "url": "https://done.example.com",
                "source": "chain",
                "priority": 42,
                "started_at": stale_started_at,
            }
        )
    }
    fake_cassandra_session.execute.side_effect = [
        MagicMock(one=MagicMock(return_value=MagicMock(status="done"))),
    ]
    out = reclaim_stale_processing_urls()
    assert out["reclaimed"] == 0
    assert fake_cassandra_session.execute.call_count == 1
    assert qid not in patch_redis_from_url.hashes.get("crawl:url_queue:processing", {})


def test_reclaim_stale_processing_urls_fails_open_on_redis_error(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Redis read failure degrades to a reported error, never a crash -- one Redis blip must not crash the beat (CLAUDE.md invariant 9)."""
    import app.modules.crawler.url_queue as uq

    def _boom() -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr(uq, "_client", _boom)
    out = reclaim_stale_processing_urls()
    assert out["status"] == "error"
    assert out["reclaimed"] == 0
    fake_cassandra_session.execute.assert_not_called()


def test_reclaim_stale_processing_urls_drops_corrupt_marker(
    fake_cassandra_session: MagicMock, patch_redis_from_url: FakeRedis
) -> None:
    """A marker that fails to parse (malformed JSON, missing fields) is dropped without touching Cassandra, so it can't wedge every future sweep."""
    qid = "00000000-0000-4000-8000-0000000000dd"
    patch_redis_from_url.hashes["crawl:url_queue:processing"] = {qid: "not json"}
    out = reclaim_stale_processing_urls()
    assert out["dropped_corrupt_markers"] == 1
    assert out["reclaimed"] == 0
    fake_cassandra_session.execute.assert_not_called()
    assert qid not in patch_redis_from_url.hashes.get("crawl:url_queue:processing", {})
