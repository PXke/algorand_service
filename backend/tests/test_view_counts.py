"""Write-path for per-article view counts (app/modules/news/stores/view_counts.py).

record_view no longer bumps Cassandra's article_view_counts counter directly
(2026-08-25) — it INCRs a "news:views:pending:{article_id}" key in Redis, and
a workers/ Celery beat task (flush_pending_views, see
workers/tests/test_view_counts_flush.py) drains those into Cassandra every 10
minutes. Reads (get_views/get_views_bulk) are unchanged and covered
elsewhere.
"""

from __future__ import annotations

from typing import Never
from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.news.stores import view_counts


class FakeRedis:
    """In-memory stand-in covering just the incr() surface record_view uses."""

    def __init__(self) -> None:
        """Start with an empty in-process key/value store."""
        self.store: dict[str, str] = {}

    def incr(self, key: str) -> int:
        """Increment a key's integer value and return the new value."""
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])


class _BoomRedis:
    """A redis client stand-in whose every call raises, simulating a Redis outage."""

    def incr(self, _key: str) -> Never:
        raise ConnectionError("redis down")


@pytest.fixture(autouse=True)
def _cassandra_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """record_view/get_views* are a no-op unless the API is configured for the Cassandra store; flip that on for this file (default is "memory")."""
    monkeypatch.setattr(settings, "news_store", "cassandra")


def test_record_view_increments_pending_redis_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A view INCRs the namespaced pending key instead of writing Cassandra directly."""
    fake = FakeRedis()
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake)
    aid = uuid4()

    view_counts.record_view(str(aid))

    assert fake.store == {f"news:views:pending:{aid}": "1"}


def test_record_view_accumulates_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated views on the same article accumulate in the same Redis key rather than each writing their own row."""
    fake = FakeRedis()
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake)
    aid = uuid4()

    view_counts.record_view(str(aid))
    view_counts.record_view(str(aid))
    view_counts.record_view(str(aid))

    assert fake.store[f"news:views:pending:{aid}"] == "3"


def test_record_view_keys_different_articles_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two different articles' pending counts don't clobber each other."""
    fake = FakeRedis()
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake)
    a1, a2 = uuid4(), uuid4()

    view_counts.record_view(str(a1))
    view_counts.record_view(str(a2))
    view_counts.record_view(str(a1))

    assert fake.store[f"news:views:pending:{a1}"] == "2"
    assert fake.store[f"news:views:pending:{a2}"] == "1"


def test_record_view_swallows_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage on the write path must never break serving the article page — record_view must not raise."""
    monkeypatch.setattr(view_counts, "_redis_client", lambda: _BoomRedis())

    view_counts.record_view(str(uuid4()))  # must not raise


def test_record_view_noop_for_invalid_article_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-UUID id (e.g. an unresolved slug) is skipped without touching Redis."""
    fake = FakeRedis()
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake)

    view_counts.record_view("not-a-uuid")

    assert fake.store == {}


def test_record_view_noop_when_not_cassandra_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-memory dev/test store: record_view is a no-op, matching the pre-existing behavior for get_views/get_views_bulk."""
    monkeypatch.setattr(settings, "news_store", "memory")
    fake = FakeRedis()
    monkeypatch.setattr(view_counts, "_redis_client", lambda: fake)

    view_counts.record_view(str(uuid4()))

    assert fake.store == {}


def test_record_view_fails_open_with_no_redis_available() -> None:
    """Belt-and-suspenders: with the test suite's process-wide socket block in effect and no monkeypatch, a real connection attempt is refused — record_view must still not raise."""
    view_counts.record_view(str(uuid4()))  # must not raise even against a real (blocked) connect
