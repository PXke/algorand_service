"""Atomic daily publish-slot reservation and rollback."""

import pytest
from conftest import FakeRedis

from app.modules.newspaper.publish_daily_guard import (
    burst_selection_today,
    lanes_used_today,
    record_burst_selection,
    record_lane_used,
    release_publish_slot,
    reserve_publish_slot,
)
from app.modules.newspaper.publish_policy import PublishTier


def test_reserve_blocks_above_cap(monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
    """Reservations succeed up to the daily cap, then the next one is rejected with a "cap" reason."""
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard._ensure_counter_initialized",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("app.core.config.NEWS_MAX_ARTICLES_PER_DAY", 2)

    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
    ok, reason = reserve_publish_slot(tier=PublishTier.STANDARD)
    assert not ok
    assert "cap" in reason


def test_release_rolls_back(monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
    """Releasing a reserved slot frees it up so a subsequent reservation can succeed."""
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard._ensure_counter_initialized",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("app.core.config.NEWS_MAX_ARTICLES_PER_DAY", 7)

    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
    release_publish_slot(tier=PublishTier.STANDARD)
    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]


def test_lanes_used_today_starts_empty(monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
    """No lanes consumed until record_lane_used is called."""
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    assert lanes_used_today() == set()


def test_record_lane_used_round_trips(monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
    """A recorded lane shows up in lanes_used_today; other lanes stay unaffected."""
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    record_lane_used("discovery")
    assert lanes_used_today() == {"discovery"}
    record_lane_used("scale")
    assert lanes_used_today() == {"discovery", "scale"}


def test_lane_usage_isolated_by_day(monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
    """Yesterday's recorded lane usage doesn't block today -- each UTC day gets its own key."""
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    yesterday = (datetime.now(tz=UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    current_day = {"value": yesterday}
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard._day_key",
        lambda when=None: current_day["value"],  # noqa: ARG005 -- test double ignores `when`
    )

    record_lane_used("human")
    current_day["value"] = today
    assert lanes_used_today() == set()


def test_burst_selection_today_starts_empty(
    monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis
) -> None:
    """No burst selected until select_daily_burst runs (record_burst_selection is called)."""
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    assert burst_selection_today() == []


def test_record_burst_selection_round_trips(
    monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis
) -> None:
    """Recorded queue_ids come back from burst_selection_today, sorted."""
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    record_burst_selection(["queue-b", "queue-a"])
    assert burst_selection_today() == ["queue-a", "queue-b"]


def test_record_burst_selection_empty_list_is_a_noop(
    monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis
) -> None:
    """An empty selection (a quiet day with nothing pending) never writes to Redis."""
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    record_burst_selection([])
    assert burst_selection_today() == []


def test_burst_selection_isolated_by_day(
    monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis
) -> None:
    """Yesterday's burst selection doesn't leak into today -- each UTC day gets its own key, same as the lane tracking."""
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    yesterday = (datetime.now(tz=UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    current_day = {"value": yesterday}
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard._day_key",
        lambda when=None: current_day["value"],  # noqa: ARG005 -- test double ignores `when`
    )

    record_burst_selection(["queue-yesterday"])
    current_day["value"] = today
    assert burst_selection_today() == []
