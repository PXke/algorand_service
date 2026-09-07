"""cache.py: get/set round-trip, asymmetric up/down freshness, fail-soft on Redis errors."""

from __future__ import annotations

from typing import Never

import pytest

from app.core.config import settings
from app.modules.x402_uptime.services import cache as cache_module
from app.modules.x402_uptime.services.checker import UptimeResult


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:  # noqa: ARG002
        self.store[key] = value
        return True


class _BrokenRedis:
    def get(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis down")

    def set(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis down")


_UP = UptimeResult(
    final_url="https://example.com/",
    reachable=True,
    http_status=200,
    response_time_ms=100,
    resolved_ip="203.0.113.5",
    error="",
    redirect_chain=["https://example.com/"],
)
_DOWN = UptimeResult(
    final_url="https://example.com/",
    reachable=False,
    http_status=0,
    response_time_ms=50,
    resolved_ip="",
    error="timeout",
    redirect_chain=["https://example.com/"],
)


@pytest.fixture(autouse=True)
def _ttls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "x402_uptime_cache_ttl_up_seconds", 180)
    monkeypatch.setattr(settings, "x402_uptime_cache_ttl_down_seconds", 30)


def test_get_cached_is_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """No prior write for this URL -- a plain cache miss."""
    monkeypatch.setattr(cache_module, "get_redis", lambda: _FakeRedis())
    assert cache_module.get_cached("https://example.com/") is None


def test_set_then_get_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    """set_cached's result is exactly what get_cached returns, cached_at included."""
    fake = _FakeRedis()
    monkeypatch.setattr(cache_module, "get_redis", lambda: fake)

    cached_at = cache_module.set_cached("https://example.com/", _UP)
    got = cache_module.get_cached("https://example.com/")

    assert got is not None
    assert got.result == _UP
    assert got.cached_at == cached_at


def test_set_cached_without_samples_defaults_to_a_single_sample_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller that never took multiple samples (e.g. a pre-percentiles test) still round-trips a usable list."""
    fake = _FakeRedis()
    monkeypatch.setattr(cache_module, "get_redis", lambda: fake)

    cache_module.set_cached("https://example.com/", _UP)
    got = cache_module.get_cached("https://example.com/")

    assert got is not None
    assert got.latency_samples_ms == [_UP.response_time_ms]


def test_set_then_get_round_trips_every_percentile_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The FULL sample list from sample_target() round-trips, not just the primary result's own latency."""
    fake = _FakeRedis()
    monkeypatch.setattr(cache_module, "get_redis", lambda: fake)

    cache_module.set_cached("https://example.com/", _UP, [90, 100, 110, 120, 130])
    got = cache_module.get_cached("https://example.com/")

    assert got is not None
    assert got.latency_samples_ms == [90, 100, 110, 120, 130]


def test_a_pre_percentiles_cache_entry_missing_the_samples_field_falls_back_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cache entry written before this field existed must not become a malformed-entry miss."""
    fake = _FakeRedis()
    monkeypatch.setattr(cache_module, "get_redis", lambda: fake)
    key = cache_module.cache_key("https://example.com/")
    fake.store[key] = cache_module.serialization.dumps(
        {
            "final_url": _UP.final_url,
            "reachable": _UP.reachable,
            "http_status": _UP.http_status,
            "response_time_ms": _UP.response_time_ms,
            "resolved_ip": _UP.resolved_ip,
            "error": _UP.error,
            "redirect_chain": _UP.redirect_chain,
            "cached_at": 1000.0,
        }
    )

    got = cache_module.get_cached("https://example.com/")

    assert got is not None
    assert got.latency_samples_ms == [_UP.response_time_ms]


def test_different_urls_hash_to_different_keys() -> None:
    """Two distinct targets never collide on the same cache key."""
    assert cache_module.cache_key("https://a.example/") != cache_module.cache_key(
        "https://b.example/"
    )


def test_read_failure_is_treated_as_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis error reading the cache fails open -- treated as no cached value, not an error."""
    monkeypatch.setattr(cache_module, "get_redis", lambda: _BrokenRedis())
    assert cache_module.get_cached("https://example.com/") is None


def test_write_failure_is_swallowed_but_still_returns_a_cached_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis error writing the cache is swallowed -- the already-paid-for result is not lost."""
    monkeypatch.setattr(cache_module, "get_redis", lambda: _BrokenRedis())
    cached_at = cache_module.set_cached("https://example.com/", _UP)
    assert isinstance(cached_at, float)


def test_malformed_cache_entry_is_treated_as_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unparseable JSON in the cache is treated as a miss, not a crash."""
    fake = _FakeRedis()
    fake.store[cache_module.cache_key("https://example.com/")] = "not json {{{"
    monkeypatch.setattr(cache_module, "get_redis", lambda: fake)
    assert cache_module.get_cached("https://example.com/") is None


def test_up_result_is_fresh_within_the_up_ttl_and_stale_after() -> None:
    """An "up" result stays a fresh hit until x402_uptime_cache_ttl_up_seconds elapses."""
    cached = cache_module.CachedCheck(result=_UP, cached_at=1000.0)
    assert cache_module.is_fresh(cached, now=1000.0 + 179) is True
    assert cache_module.is_fresh(cached, now=1000.0 + 181) is False


def test_down_result_uses_the_shorter_ttl() -> None:
    """A "down" result goes stale after the shorter down-TTL, well before the up-TTL would expire it."""
    cached = cache_module.CachedCheck(result=_DOWN, cached_at=1000.0)
    assert cache_module.is_fresh(cached, now=1000.0 + 29) is True
    assert cache_module.is_fresh(cached, now=1000.0 + 31) is False
    # The same age that's still fresh for an "up" result is already stale
    # for a "down" one -- the whole point of the asymmetry.
    up_cached = cache_module.CachedCheck(result=_UP, cached_at=1000.0)
    assert cache_module.is_fresh(up_cached, now=1000.0 + 31) is True


def test_5xx_counts_as_down_for_freshness_purposes() -> None:
    """A reachable-but-5xx result uses the short down-TTL, not the long up-TTL."""
    server_error = UptimeResult(
        final_url="https://example.com/",
        reachable=True,
        http_status=503,
        response_time_ms=10,
        resolved_ip="203.0.113.5",
        error="",
        redirect_chain=["https://example.com/"],
    )
    assert cache_module.is_down(server_error) is True


def test_is_down_fields_is_the_single_source_of_truth_is_down_delegates_to() -> None:
    """is_down_fields must agree with is_down() on every case.

    history_service.py's aggregation reuses is_down_fields directly (raw
    Cassandra columns, not a checker.UptimeResult).
    """
    assert cache_module.is_down_fields(reachable=False, http_status=0) is True
    assert cache_module.is_down_fields(reachable=True, http_status=503) is True
    assert cache_module.is_down_fields(reachable=True, http_status=200) is False
