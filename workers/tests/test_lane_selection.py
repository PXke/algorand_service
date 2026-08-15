"""Daily lane selection for drain_standard_publish_queue: which of the 3 slots (human pick / biggest-significant / genuinely-new) the next fresh compose should draw from."""

from __future__ import annotations

import pytest

from app.modules.newspaper.publish_policy import PublishKind
from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.newspaper.tasks.queue_drain_tasks import (
    _row_matches_lane,
    _select_lane_for_today,
    _today_str,
)


def _row(
    *, publish_kind: str = PublishKind.CONTENT_UPDATE.value, human_pick_day: str | None = None
) -> QueuedPublishRow:
    return QueuedPublishRow(
        queue_id="q1",
        priority=10,
        topic="content_update",
        publish_kind=publish_kind,
        service_id="svc",
        display_name="Some Service",
        scrape_url="https://example.com",
        payload={},
        created_at_epoch=0,
        human_pick_day=human_pick_day,
    )


def _no_lanes_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.lanes_used_today", lambda: set()
    )


def _lanes_used(monkeypatch: pytest.MonkeyPatch, used: set[str]) -> None:
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.lanes_used_today", lambda: used
    )


def test_selects_human_when_pin_exists_and_unused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A human pick always wins when present and unused today."""
    _no_lanes_used(monkeypatch)
    pending = [_row(human_pick_day=_today_str()), _row(publish_kind=PublishKind.SERVICE_DISCOVERY.value)]
    assert _select_lane_for_today(pending) == "human"


def test_stale_pin_from_a_different_day_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pin left over from a day it was never consumed on must not jump today's queue."""
    _no_lanes_used(monkeypatch)
    pending = [_row(human_pick_day="2020-01-01"), _row(publish_kind=PublishKind.SERVICE_DISCOVERY.value)]
    assert _select_lane_for_today(pending) == "discovery"


def test_falls_to_discovery_when_no_pin_and_discovery_unused(monkeypatch: pytest.MonkeyPatch) -> None:
    """No human pin -- falls to discovery when its slot is still open."""
    _no_lanes_used(monkeypatch)
    pending = [_row(publish_kind=PublishKind.SERVICE_DISCOVERY.value), _row()]
    assert _select_lane_for_today(pending) == "discovery"


def test_falls_to_scale_when_human_and_discovery_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once human and discovery are both spent, scale takes the slot."""
    _lanes_used(monkeypatch, {"human", "discovery"})
    pending = [_row(publish_kind=PublishKind.SERVICE_DISCOVERY.value), _row()]
    assert _select_lane_for_today(pending) == "scale"


def test_returns_none_when_all_three_lanes_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """All 3 lanes spent for the day -- falls back to the plain priority order."""
    _lanes_used(monkeypatch, {"human", "discovery", "scale"})
    pending = [_row(publish_kind=PublishKind.SERVICE_DISCOVERY.value), _row(human_pick_day=_today_str())]
    assert _select_lane_for_today(pending) is None


def test_returns_none_when_a_lanes_pool_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery unused but no pending discovery row exists -- falls through to scale, not stuck."""
    _no_lanes_used(monkeypatch)
    pending = [_row()]  # only a content-update row, no discovery, no human pin
    assert _select_lane_for_today(pending) == "scale"


def test_a_used_lane_is_not_reselected_same_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lane already recorded as used today is skipped in favor of the next open lane."""
    _lanes_used(monkeypatch, {"discovery"})
    pending = [_row(publish_kind=PublishKind.SERVICE_DISCOVERY.value), _row()]
    assert _select_lane_for_today(pending) == "scale"


def test_row_matches_lane_human() -> None:
    """Only today's pin matches the human lane; None/a stale day don't."""
    assert _row_matches_lane(_row(human_pick_day=_today_str()), "human")
    assert not _row_matches_lane(_row(human_pick_day=None), "human")
    assert not _row_matches_lane(_row(human_pick_day="2020-01-01"), "human")


def test_row_matches_lane_discovery() -> None:
    """Only service_discovery rows match the discovery lane."""
    assert _row_matches_lane(_row(publish_kind=PublishKind.SERVICE_DISCOVERY.value), "discovery")
    assert not _row_matches_lane(_row(publish_kind=PublishKind.CONTENT_UPDATE.value), "discovery")


def test_row_matches_lane_scale() -> None:
    """Only non-discovery rows match the scale lane."""
    assert _row_matches_lane(_row(publish_kind=PublishKind.CONTENT_UPDATE.value), "scale")
    assert not _row_matches_lane(_row(publish_kind=PublishKind.SERVICE_DISCOVERY.value), "scale")
