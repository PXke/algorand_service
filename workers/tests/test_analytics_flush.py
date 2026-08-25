"""flush_pending_analytics (workers/app/modules/newspaper/analytics_flush.py).

Drains the "news:analytics:pending:{dim}:{hex-encoded parts}" Redis keys
backend's _buffer_pending_analytics INCRs for six deferred pageview-analytics
dimensions (geo, campaign, hour, language, referrer_path, referrer_url) into
their Cassandra counter tables, on a 10-minute Celery beat
(celery_app.py's "flush-pending-analytics"). Same shape as
test_view_counts_flush.py for article_view_counts.
"""

from __future__ import annotations

import fnmatch
from typing import Never
from unittest.mock import MagicMock

import pytest

from app.core.statements import AnalyticsFlushStmts
from app.modules.newspaper import analytics_flush


class FakeFlushRedis:
    """In-memory stand-in covering the scan_iter/getdel/delete surface flush_pending_analytics uses."""

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
    def scan_iter(self, match: str, count: int = 200) -> Never:  # noqa: ARG002
        raise ConnectionError("redis down")


def _hex(*parts: str) -> str:
    return ":".join(p.encode().hex() for p in parts)


def _key(dim: str, *parts: str) -> str:
    return f"{analytics_flush.PENDING_PREFIX}{dim}:{_hex(*parts)}"


def test_flush_applies_geo_delta_and_clears_redis(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending geo key's accumulated count applies as one batched bump, then clears from Redis."""
    fake_redis = FakeFlushRedis({_key("geo", "2026-08-25", "US"): "4"})
    monkeypatch.setattr(analytics_flush, "_redis_client", lambda: fake_redis)

    result = analytics_flush.flush_pending_analytics()

    assert result == {"applied": 1, "skipped": 0}
    assert fake_redis.store == {}
    fake_cassandra_session.execute.assert_called_once_with(
        AnalyticsFlushStmts.GEO_BUMP, (4, "2026-08-25", "US")
    )


def test_flush_applies_hour_delta_with_int_cast(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hour dimension's second key part is cast back to int (pageview_hour_daily.hour is an int column)."""
    fake_redis = FakeFlushRedis({_key("hour", "2026-08-25", "14"): "9"})
    monkeypatch.setattr(analytics_flush, "_redis_client", lambda: fake_redis)

    result = analytics_flush.flush_pending_analytics()

    assert result == {"applied": 1, "skipped": 0}
    fake_cassandra_session.execute.assert_called_once_with(
        AnalyticsFlushStmts.HOUR_BUMP, (9, "2026-08-25", 14)
    )


def test_flush_applies_all_six_dimensions(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every dimension's pending key gets its own correctly-shaped Cassandra bump."""
    day = "2026-08-25"
    fake_redis = FakeFlushRedis(
        {
            _key("geo", day, "FR"): "1",
            _key("campaign", day, "twitter / launch"): "2",
            _key("hour", day, "23"): "3",
            _key("language", day, "fr"): "4",
            _key("referrer_path", day, "reddit.com", "/news/articles/x"): "5",
            _key("referrer_url", day, "reddit.com/r/algorand"): "6",
        }
    )
    monkeypatch.setattr(analytics_flush, "_redis_client", lambda: fake_redis)

    result = analytics_flush.flush_pending_analytics()

    assert result == {"applied": 6, "skipped": 0}
    assert fake_redis.store == {}
    # Matched by params content, not statement identity: fake_cassandra_session
    # is a MagicMock, so every AnalyticsFlushStmts.* attribute access resolves
    # to the SAME shared session.prepare.return_value object regardless of
    # which CQL string was prepared -- real preparation caches per distinct
    # CQL text (see prepare_cached), but that distinction doesn't exist on a
    # mock, so params are the only thing that can tell the six calls apart here.
    calls = [c.args[1] for c in fake_cassandra_session.execute.call_args_list]
    assert len(calls) == 6
    assert (1, day, "FR") in calls
    assert (2, day, "twitter / launch") in calls
    assert (3, day, 23) in calls
    assert (4, day, "fr") in calls
    assert (5, day, "reddit.com", "/news/articles/x") in calls
    assert (6, day, "reddit.com/r/algorand") in calls


def test_flush_preserves_colon_bearing_parts(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A referrer_url part containing ':' (e.g. a scheme-bearing or otherwise unusual value) round-trips exactly through the hex encoding, never mistaken for a field separator."""
    day = "2026-08-25"
    tricky_url = "example.com/path?x=a:b:c"
    fake_redis = FakeFlushRedis({_key("referrer_url", day, tricky_url): "1"})
    monkeypatch.setattr(analytics_flush, "_redis_client", lambda: fake_redis)

    result = analytics_flush.flush_pending_analytics()

    assert result == {"applied": 1, "skipped": 0}
    fake_cassandra_session.execute.assert_called_once_with(
        AnalyticsFlushStmts.REFERRER_URL_BUMP, (1, day, tricky_url)
    )


def test_flush_no_pending_keys_is_a_noop(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty scan touches Cassandra zero times."""
    fake_redis = FakeFlushRedis({})
    monkeypatch.setattr(analytics_flush, "_redis_client", lambda: fake_redis)

    result = analytics_flush.flush_pending_analytics()

    assert result == {"applied": 0, "skipped": 0}
    fake_cassandra_session.execute.assert_not_called()


def test_flush_swallows_redis_scan_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage flushes nothing this cycle -- must never raise into the beat task."""
    monkeypatch.setattr(analytics_flush, "_redis_client", lambda: _BoomScanRedis())

    result = analytics_flush.flush_pending_analytics()  # must not raise

    assert result == {"applied": 0, "skipped": 0}


def test_flush_drops_malformed_key_without_jamming_the_cycle(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key with an unknown dimension, wrong arity, or unparseable hex is deleted outright, and the rest of the batch still applies."""
    good = _key("geo", "2026-08-25", "DE")
    fake_redis = FakeFlushRedis(
        {
            f"{analytics_flush.PENDING_PREFIX}unknown_dim:{_hex('x', 'y')}": "7",
            f"{analytics_flush.PENDING_PREFIX}geo:{_hex('only-one-part')}": "1",  # wrong arity
            f"{analytics_flush.PENDING_PREFIX}geo:not-hex:also-not-hex": "1",  # bad hex
            good: "2",
        }
    )
    monkeypatch.setattr(analytics_flush, "_redis_client", lambda: fake_redis)

    result = analytics_flush.flush_pending_analytics()

    assert result == {"applied": 1, "skipped": 3}
    assert fake_redis.store == {}
    fake_cassandra_session.execute.assert_called_once_with(
        AnalyticsFlushStmts.GEO_BUMP, (2, "2026-08-25", "DE")
    )


def test_flush_one_cassandra_failure_does_not_lose_other_keys(
    fake_cassandra_session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-key Cassandra failure is skipped without aborting the rest of the batch already in flight."""
    fake_redis = FakeFlushRedis(
        {
            _key("geo", "2026-08-25", "US"): "4",
            _key("language", "2026-08-25", "en"): "9",
        }
    )
    monkeypatch.setattr(analytics_flush, "_redis_client", lambda: fake_redis)
    fake_cassandra_session.execute.side_effect = [ConnectionError("cassandra down"), MagicMock()]

    result = analytics_flush.flush_pending_analytics()

    assert result == {"applied": 1, "skipped": 1}
    assert fake_cassandra_session.execute.call_count == 2
