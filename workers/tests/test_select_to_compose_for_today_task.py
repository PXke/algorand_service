"""select_to_compose_for_today_task must be a no-op once a day's slate is already populated.

Root-caused 2026-08-27: `select_to_compose_for_day` only DELETEs+re-picks a
day's `to_compose` rows -- it does not revert already-SELECTED artifacts back
to pending the way `reset_and_reselect_for_day` does. Calling it
unconditionally on an already-populated day (e.g. one an admin "Redo"/pin
action populated for tomorrow, which then becomes "today" when the beat
fires) silently drops that day's human pin (selection only scans PENDING
artifacts) and strands its platform picks as SELECTED-with-no-to_compose-row
-- invisible to both a later Redo and to `reclaim_stale_selected_artifacts`.
"""

from __future__ import annotations

import pytest
from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day
from algorand_shared.to_compose_selection import list_to_compose_for_day, select_to_compose_for_day

from app.modules.newspaper.tasks import queue_drain_tasks as qdt


@pytest.mark.usefixtures("fake_artifact_session")
def test_beat_is_a_noop_when_todays_slate_already_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    today = qdt._today_str()
    pinned_id, _ = insert_artifact(service_id="svc-pin", url=None, channel="brief", content="pinned")
    insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    pin_artifact_for_day(pinned_id, today)

    original = select_to_compose_for_day(today)
    assert original["human_picked"] is True

    result = qdt.select_to_compose_for_today_task()

    assert result == {
        "status": "skipped",
        "reason": "already_selected",
        "compose_day": today,
        "existing_slots": len(list_to_compose_for_day(today)),
    }
    lanes_after = [row["lane"] for row in list_to_compose_for_day(today)]
    assert "human" in lanes_after


@pytest.mark.usefixtures("fake_artifact_session")
def test_beat_selects_normally_when_no_slate_exists_yet() -> None:
    today = qdt._today_str()
    insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")

    result = qdt.select_to_compose_for_today_task()

    assert result["status"] == "ok"
    assert list_to_compose_for_day(today)
