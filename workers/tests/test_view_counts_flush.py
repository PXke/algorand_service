"""flush_pending_views (workers/app/modules/newspaper/view_counts.py).

Drains the "news:views:pending:{article_id}" Redis keys backend's record_view
INCRs on every article page view (see backend/tests/test_view_counts.py) onto
articles.views (migration 084 -- a plain int column, replacing the old
article_view_counts counter table) on a 10-minute Celery beat
(celery_app.py's "flush-pending-view-counts"). Since it's a plain column, not
a counter, each flush is read-current-total + add-delta + write-back via
article_store.get_article_views/update_article_views.
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


def _patch_article_store(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current: dict[UUID, int | None] | None = None,
    update_ok: bool = True,
    update_error: bool = False,
) -> MagicMock:
    """Stub get_article_views/update_article_views as flush_pending_views imports them."""
    current = current or {}
    update_mock = MagicMock()

    def fake_get(raw_id: str) -> int | None:
        return current.get(UUID(raw_id), 0)

    def fake_update(raw_id: str, views: int) -> bool:
        if update_error:
            raise ConnectionError("cassandra down")
        update_mock(raw_id, views)
        return update_ok

    import app.modules.newspaper.article_store as article_store

    monkeypatch.setattr(article_store, "get_article_views", fake_get)
    monkeypatch.setattr(article_store, "update_article_views", fake_update)
    return update_mock


def test_flush_pending_views_applies_delta_onto_current_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending key's accumulated count is added to the article's current tally and written back, then cleared from Redis."""
    aid = uuid4()
    fake_redis = FakeFlushRedis({_key(aid): "3"})
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake_redis)
    update_mock = _patch_article_store(monkeypatch, current={aid: 7})

    result = view_counts.flush_pending_views()

    assert result == {"applied": 1, "skipped": 0}
    assert fake_redis.store == {}
    update_mock.assert_called_once_with(str(aid), 10)


def test_flush_pending_views_applies_each_of_several_articles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every distinct pending key in the scan gets its own read-current + write-back."""
    a1, a2 = uuid4(), uuid4()
    fake_redis = FakeFlushRedis({_key(a1): "5", _key(a2): "1"})
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake_redis)
    update_mock = _patch_article_store(monkeypatch, current={a1: 0, a2: 0})

    result = view_counts.flush_pending_views()

    assert result == {"applied": 2, "skipped": 0}
    assert fake_redis.store == {}
    assert update_mock.call_count == 2


def test_flush_pending_views_no_pending_keys_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty scan touches Cassandra zero times."""
    fake_redis = FakeFlushRedis({})
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake_redis)
    update_mock = _patch_article_store(monkeypatch)

    result = view_counts.flush_pending_views()

    assert result == {"applied": 0, "skipped": 0}
    update_mock.assert_not_called()


def test_flush_pending_views_swallows_redis_scan_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage flushes nothing this cycle; increments stay parked in Redis for the next run -- must never raise into the beat task."""
    monkeypatch.setattr(view_counts, "_redis_client", lambda: _BoomScanRedis())

    result = view_counts.flush_pending_views()  # must not raise

    assert result == {"applied": 0, "skipped": 0}


def test_flush_pending_views_drops_malformed_key_without_jamming_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key that somehow isn't a valid UUID suffix is deleted outright (so it can't jam every future cycle) and the rest of the batch still applies."""
    good = uuid4()
    fake_redis = FakeFlushRedis(
        {f"{view_counts.VIEW_PENDING_PREFIX}not-a-uuid": "7", _key(good): "2"}
    )
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake_redis)
    update_mock = _patch_article_store(monkeypatch, current={good: 0})

    result = view_counts.flush_pending_views()

    assert result == {"applied": 1, "skipped": 1}
    assert fake_redis.store == {}
    update_mock.assert_called_once()


def test_flush_pending_views_one_cassandra_failure_does_not_lose_other_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A per-key Cassandra failure is skipped without aborting the rest of the batch already in flight."""
    a1, a2 = uuid4(), uuid4()
    fake_redis = FakeFlushRedis({_key(a1): "4", _key(a2): "9"})
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake_redis)

    import app.modules.newspaper.article_store as article_store

    def fake_get(raw_id: str) -> int | None:
        if UUID(raw_id) == a1:
            raise ConnectionError("cassandra down")
        return 0

    monkeypatch.setattr(article_store, "get_article_views", fake_get)
    monkeypatch.setattr(article_store, "update_article_views", lambda *_a: True)

    result = view_counts.flush_pending_views()

    assert result == {"applied": 1, "skipped": 1}


def test_flush_pending_views_drops_delta_for_a_fully_purged_article(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_article_views returning None (no such article any more) drops the delta instead of re-parking it, so it can't jam every future cycle."""
    aid = uuid4()
    fake_redis = FakeFlushRedis({_key(aid): "6"})
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake_redis)
    update_mock = _patch_article_store(monkeypatch, current={aid: None})

    result = view_counts.flush_pending_views()

    assert result == {"applied": 0, "skipped": 1}
    assert fake_redis.store == {}
    update_mock.assert_not_called()
