"""flush_pending_views (workers/app/modules/newspaper/view_counts.py).

Drains the "news:views:pending:{article_id}" Redis keys backend's record_view
INCRs on every article page view (see backend/tests/test_view_counts.py) into
Cassandra's article_view_counts counter table, on a 10-minute Celery beat
(celery_app.py's "flush-pending-view-counts").
"""

from __future__ import annotations

import fnmatch
from typing import Never
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.modules.newspaper import view_counts


class FakeFlushRedis:
    """In-memory stand-in covering the scan_iter/getdel/delete surface flush_pending_views uses.

    Deliberately has no plain get() -- if the implementation ever regressed to
    a non-atomic get-then-delete, a test exercising that path would fail loudly
    with AttributeError instead of silently passing.
    """

    def __init__(self, store: dict[str, str] | None = None) -> None:
        """Start from the given key/value store (or empty)."""
        self.store: dict[str, str] = dict(store or {})

    def scan_iter(self, match: str, count: int = 200) -> list[str]:  # noqa: ARG002
        """Return every stored key matching the glob pattern."""
        return [key for key in list(self.store) if fnmatch.fnmatch(key, match)]

    def getdel(self, key: str) -> str | None:
        """Atomically pop and return a key's value (None if absent)."""
        return self.store.pop(key, None)

    def delete(self, *keys: str) -> int:
        """Delete the given keys, returning the count actually removed."""
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed


class _BoomScanRedis:
    """A redis client stand-in whose scan_iter always raises, simulating a Redis outage."""

    def scan_iter(self, match: str, count: int = 200) -> Never:  # noqa: ARG002
        """Always raise, as if Redis were unreachable."""
        raise ConnectionError("redis down")


def _key(article_id: UUID) -> str:
    return f"{view_counts.VIEW_PENDING_PREFIX}{article_id}"


def test_flush_pending_views_applies_delta_and_clears_redis(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending key's accumulated count is applied as one batched Cassandra bump, then cleared from Redis."""
    aid = uuid4()
    fake_redis = FakeFlushRedis({_key(aid): "3"})
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake_redis)

    result = view_counts.flush_pending_views()

    assert result == {"applied": 1, "skipped": 0}
    assert fake_redis.store == {}
    from app.core.statements import ViewCountStmts

    fake_cassandra_session.execute.assert_called_once_with(ViewCountStmts.BUMP, (3, aid))


def test_flush_pending_views_applies_each_of_several_articles(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every distinct pending key in the scan gets its own Cassandra bump."""
    a1, a2 = uuid4(), uuid4()
    fake_redis = FakeFlushRedis({_key(a1): "5", _key(a2): "1"})
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake_redis)

    result = view_counts.flush_pending_views()

    assert result == {"applied": 2, "skipped": 0}
    assert fake_redis.store == {}
    assert fake_cassandra_session.execute.call_count == 2


def test_flush_pending_views_no_pending_keys_is_a_noop(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty scan touches Cassandra zero times."""
    fake_redis = FakeFlushRedis({})
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake_redis)

    result = view_counts.flush_pending_views()

    assert result == {"applied": 0, "skipped": 0}
    fake_cassandra_session.execute.assert_not_called()


def test_flush_pending_views_swallows_redis_scan_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage flushes nothing this cycle; increments stay parked in Redis for the next run -- must never raise into the beat task."""
    monkeypatch.setattr(view_counts, "_redis_client", lambda: _BoomScanRedis())

    result = view_counts.flush_pending_views()  # must not raise

    assert result == {"applied": 0, "skipped": 0}


def test_flush_pending_views_drops_malformed_key_without_jamming_the_cycle(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key that somehow isn't a valid UUID suffix is deleted outright (so it can't jam every future cycle) and the rest of the batch still applies."""
    good = uuid4()
    fake_redis = FakeFlushRedis(
        {f"{view_counts.VIEW_PENDING_PREFIX}not-a-uuid": "7", _key(good): "2"}
    )
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake_redis)

    result = view_counts.flush_pending_views()

    assert result == {"applied": 1, "skipped": 1}
    assert fake_redis.store == {}
    fake_cassandra_session.execute.assert_called_once()


def test_flush_pending_views_one_cassandra_failure_does_not_lose_other_keys(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-key Cassandra failure is skipped without aborting the rest of the batch already in flight."""
    a1, a2 = uuid4(), uuid4()
    fake_redis = FakeFlushRedis({_key(a1): "4", _key(a2): "9"})
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake_redis)
    fake_cassandra_session.execute.side_effect = [ConnectionError("cassandra down"), MagicMock()]

    result = view_counts.flush_pending_views()

    assert result == {"applied": 1, "skipped": 1}
    assert fake_cassandra_session.execute.call_count == 2
