"""One-day-ahead burst compose: daily selection (select_daily_burst) and the off-peak batch compose (burst_compose_today) that consumes it."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.modules.newspaper.publish_policy import PublishKind
from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.newspaper.tasks.burst_compose_tasks import (
    burst_compose_today,
    select_daily_burst,
)


def _row(
    *,
    queue_id: str = "q1",
    priority: int = 10,
    publish_kind: str = PublishKind.CONTENT_UPDATE.value,
    human_pick_day: str | None = None,
) -> QueuedPublishRow:
    return QueuedPublishRow(
        queue_id=queue_id,
        priority=priority,
        topic="content_update",
        publish_kind=publish_kind,
        service_id="svc",
        display_name="Some Service",
        scrape_url="https://example.com",
        payload={},
        created_at_epoch=0,
        human_pick_day=human_pick_day,
    )


def test_select_daily_burst_skips_when_auto_compose_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The global AUTO_COMPOSE_PAUSED flag stops selection just like every other beat-scheduled compose-adjacent task."""
    import app.core.config as config

    monkeypatch.setattr(config, "AUTO_COMPOSE_PAUSED", True)
    result = select_daily_burst()
    assert result == {"status": "skipped", "reason": "auto_compose_paused"}


def test_select_daily_burst_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second run the same day is a no-op -- it must never re-select or double-mark rows."""
    import app.core.config as config

    monkeypatch.setattr(config, "AUTO_COMPOSE_PAUSED", False)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.burst_selection_today",
        lambda: ["q1", "q2"],
    )
    result = select_daily_burst()
    assert result == {"status": "already_selected", "queue_ids": ["q1", "q2"]}


def test_select_daily_burst_picks_human_discovery_and_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Picks the human pin (if any) + the top-priority discovery row + the top-priority scale row, and records exactly those three."""
    import app.core.config as config
    from app.modules.newspaper.publish_queue_store import PublishTier

    monkeypatch.setattr(config, "AUTO_COMPOSE_PAUSED", False)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.burst_selection_today", lambda: []
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard._day_key", lambda when=None: "2026-08-16"  # noqa: ARG005
    )
    recorded: dict = {}
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.record_burst_selection",
        lambda ids: recorded.setdefault("ids", ids),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.queue_row_tier",
        lambda _row: PublishTier.STANDARD,
    )

    human_id = "11111111-1111-1111-1111-111111111111"
    discovery_low_id = "22222222-2222-2222-2222-222222222222"
    discovery_high_id = "33333333-3333-3333-3333-333333333333"
    scale_low_id = "44444444-4444-4444-4444-444444444444"
    scale_high_id = "55555555-5555-5555-5555-555555555555"
    human = _row(queue_id=human_id, human_pick_day="2026-08-16")
    discovery_low = _row(
        queue_id=discovery_low_id,
        priority=50,
        publish_kind=PublishKind.SERVICE_DISCOVERY.value,
    )
    discovery_high = _row(
        queue_id=discovery_high_id,
        priority=200,
        publish_kind=PublishKind.SERVICE_DISCOVERY.value,
    )
    scale_low = _row(queue_id=scale_low_id, priority=80)
    scale_high = _row(queue_id=scale_high_id, priority=300)
    pending = [human, discovery_low, discovery_high, scale_low, scale_high]
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.list_pending_queue", lambda **_kw: pending
    )

    fake_session = MagicMock()
    with patch("app.core.cassandra.get_cassandra_session", lambda: fake_session):
        result = select_daily_burst()

    assert result["status"] == "selected"
    assert set(result["queue_ids"]) == {human_id, discovery_high_id, scale_high_id}
    assert set(recorded["ids"]) == {human_id, discovery_high_id, scale_high_id}
    # Marked in Cassandra, not just recorded in Redis.
    assert fake_session.execute.call_count == 3


def test_select_daily_burst_handles_nothing_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """A quiet day (nothing pending at all) selects nothing rather than erroring."""
    import app.core.config as config
    from app.modules.newspaper.publish_queue_store import PublishTier

    monkeypatch.setattr(config, "AUTO_COMPOSE_PAUSED", False)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.burst_selection_today", lambda: []
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.queue_row_tier",
        lambda _row: PublishTier.STANDARD,
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.list_pending_queue", lambda **_kw: []
    )

    result = select_daily_burst()
    assert result == {"status": "nothing_pending"}


def test_burst_compose_today_skips_when_auto_compose_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same global pause flag guards the compose step too."""
    import app.core.config as config

    monkeypatch.setattr(config, "AUTO_COMPOSE_PAUSED", True)
    assert burst_compose_today() == {"status": "skipped", "reason": "auto_compose_paused"}


def test_burst_compose_today_no_selection_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing to do if select_daily_burst hasn't run yet today."""
    import app.core.config as config

    monkeypatch.setattr(config, "AUTO_COMPOSE_PAUSED", False)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.burst_selection_today", lambda: []
    )
    assert burst_compose_today() == {"status": "nothing_selected"}


def test_burst_compose_today_composes_each_selected_row_and_stamps_the_article(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calls compose_queue_row_now for every selected queue_id (reusing the existing admin single-row compose path + its gates) and stamps burst_day on each resulting article."""
    import app.core.config as config

    monkeypatch.setattr(config, "AUTO_COMPOSE_PAUSED", False)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.burst_selection_today",
        lambda: ["q1", "q2"],
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard._day_key", lambda when=None: "2026-08-16"  # noqa: ARG005
    )

    outcomes = {
        "q1": {
            "status": "review",
            "article_id": "11111111-1111-1111-1111-111111111111",
            "review_id": "rev-1",
        },
        # A vetoed row (no article ever created) must not blow up the stamp step.
        "q2": {"status": "duplicate", "service_id": "svc"},
    }
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.publish_tasks.compose_queue_row_now",
        lambda queue_id: outcomes[queue_id],
    )

    fake_session = MagicMock()
    with patch("app.core.cassandra.get_cassandra_session", lambda: fake_session):
        result = burst_compose_today()

    assert result["status"] == "done"
    assert [r["queue_id"] for r in result["results"]] == ["q1", "q2"]
    # Only the row that actually produced an article gets stamped.
    assert fake_session.execute.call_count == 1
    args = fake_session.execute.call_args[0]
    assert args[1][0] == "2026-08-16"
