"""CassandraArticleStore.tag_summary(): caching behavior.

The per-tag COUNT + LIST_RECENT fan-out is cached (app.core.cache.cached_json),
not re-run on every call.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import patch_cassandra

from app.modules.news.stores.cassandra import CassandraArticleStore


class FakeRedis:
    """In-memory stand-in for the redis-py client, covering the get/set surface app.core.cache uses."""

    def __init__(self) -> None:
        """Start with an empty in-process key/value store."""
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        """Return the stored value for a key, or None if absent."""
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:  # noqa: ARG002 -- ex accepted, not enforced (no real TTL in-memory)
        """Set a key's value; ex (TTL) is accepted but not enforced."""
        self.store[key] = value
        return True


class _TagRow:
    def __init__(self, tag: str) -> None:
        self.tag = tag


class _SampleRow:
    def __init__(self, article_id: str, published_at: datetime) -> None:
        self.article_id = article_id
        self.published_at = published_at


class _CountRow:
    def __init__(self, count: int) -> None:
        self.count = count


class _CountResult:
    def __init__(self, count: int) -> None:
        self._count = count

    def one(self) -> _CountRow:
        return _CountRow(self._count)


class _FakeSession:
    """Fakes the one LIST_TAGS DISTINCT-scan call tag_summary makes directly on the session."""

    def __init__(self, tags: list[str]) -> None:
        self._tags = tags
        self.execute_calls = 0

    def execute(self, _stmt: object, _params: object = None) -> list[_TagRow]:
        self.execute_calls += 1
        return [_TagRow(t) for t in self._tags]


@pytest.fixture
def _fake_cache_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Back app.core.cache's Redis client with an in-memory fake."""
    fake = FakeRedis()
    monkeypatch.setattr("app.core.cache._client", lambda: fake)
    return fake


@pytest.mark.usefixtures("_fake_cache_redis")
def test_tag_summary_second_call_within_ttl_skips_the_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second tag_summary() call within the cache TTL must not re-run the LIST_TAGS scan or the per-tag COUNT/LIST_RECENT fan-out."""
    session = _FakeSession(["defi", "nft"])
    patch_cassandra(monkeypatch, session)

    fanout_calls: list[str] = []

    def fake_execute_parallel_with_args(
        _stmt: object, args_list: list[tuple], *, raise_on_error: bool = False
    ) -> list[tuple[bool, object]]:
        _ = raise_on_error
        results: list[tuple[bool, object]] = []
        for args in args_list:
            if len(args) == 1:
                fanout_calls.append(f"count:{args[0]}")
                results.append((True, _CountResult(3)))
            else:
                fanout_calls.append(f"sample:{args[0]}")
                results.append((True, [_SampleRow("a1", datetime(2026, 8, 1, tzinfo=UTC))]))
        return results

    monkeypatch.setattr(
        "app.core.cassandra.execute_parallel_with_args", fake_execute_parallel_with_args
    )

    store = CassandraArticleStore()
    first = store.tag_summary()
    second = store.tag_summary()

    # One LIST_TAGS scan + one COUNT/LIST_RECENT fan-out per tag on the FIRST
    # call only -- the second call must be served entirely from cache.
    assert session.execute_calls == 1
    assert fanout_calls == ["count:defi", "count:nft", "sample:defi", "sample:nft"]

    assert [s.tag for s in second] == [s.tag for s in first] == ["defi", "nft"]
    assert all(s.count == 3 for s in second)
    assert all(s.article_ids == ["a1"] for s in second)


@pytest.mark.usefixtures("_fake_cache_redis")
def test_tag_summary_different_sample_limit_is_not_served_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call with a different `sample_limit` must not be served the other sample_limit's cached entry."""
    session = _FakeSession(["defi"])
    patch_cassandra(monkeypatch, session)

    fanout_calls: list[int] = []

    def fake_execute_parallel_with_args(
        _stmt: object, args_list: list[tuple], *, raise_on_error: bool = False
    ) -> list[tuple[bool, object]]:
        _ = raise_on_error
        results: list[tuple[bool, object]] = []
        for args in args_list:
            if len(args) == 1:
                results.append((True, _CountResult(1)))
            else:
                fanout_calls.append(args[1])
                results.append((True, []))
        return results

    monkeypatch.setattr(
        "app.core.cassandra.execute_parallel_with_args", fake_execute_parallel_with_args
    )

    store = CassandraArticleStore()
    store.tag_summary(sample_limit=50)
    store.tag_summary(sample_limit=200)

    # Both sample_limit values triggered their own fan-out -- separate cache keys.
    assert fanout_calls == [50, 200]
