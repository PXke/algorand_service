"""The five on-demand Celery task wrappers artifact_tasks.py adds for the admin dashboard: preview_to_compose_for_day (live forecast), list_to_compose_for_day (the real persisted selection), pin_artifact_for_tomorrow (writes a human pin), get_artifact_detail (full title/content/url for one artifact, on expand), and reset_and_reselect_to_compose_for_day (the "Redo today's picks" action).

Confirms each task body is a thin delegation to its already-tested
to_compose_selection / artifact_store function (preview_to_compose_for_day /
list_to_compose_for_day / pin_for_tomorrow / get_artifact+get_artifact_content /
reset_and_reselect_for_day) and that all five are reachable under their
registered task names -- the backend admin routes dispatch by name via
Celery.send_task, so a drifted name would 404 silently at runtime with no
import-time signal.
"""

from __future__ import annotations

import pytest


def test_preview_task_delegates_to_preview_to_compose_for_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Celery task body is a thin delegation to to_compose_selection.preview_to_compose_for_day."""
    from app.modules.newspaper.tasks import artifact_tasks

    called = {}

    def _fake_preview(day: str) -> dict[str, object]:
        called["day"] = day
        return {"status": "ok", "compose_day": day, "items": []}

    monkeypatch.setattr(
        "app.modules.newspaper.to_compose_selection.preview_to_compose_for_day", _fake_preview
    )
    result = artifact_tasks.preview_to_compose_for_day.run("2026-08-26")

    assert called["day"] == "2026-08-26"
    assert result == {"status": "ok", "compose_day": "2026-08-26", "items": []}


def test_list_selected_task_delegates_to_list_to_compose_for_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Celery task body is a thin delegation to to_compose_selection.list_to_compose_for_day."""
    from app.modules.newspaper.tasks import artifact_tasks

    called = {}

    def _fake_list(day: str) -> list[dict[str, object]]:
        called["day"] = day
        return [{"slot": 0, "artifact_id": "abc", "lane": "human", "service_id": "svc-a"}]

    monkeypatch.setattr(
        "app.modules.newspaper.to_compose_selection.list_to_compose_for_day", _fake_list
    )
    result = artifact_tasks.list_to_compose_for_day.run("2026-08-26")

    assert called["day"] == "2026-08-26"
    assert result == [{"slot": 0, "artifact_id": "abc", "lane": "human", "service_id": "svc-a"}]


def test_list_selected_task_returns_empty_before_the_beat_has_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty `to_compose` table (the daily beat hasn't fired for this day yet) surfaces as an empty list, not an error."""
    from app.modules.newspaper.tasks import artifact_tasks

    monkeypatch.setattr(
        "app.modules.newspaper.to_compose_selection.list_to_compose_for_day", lambda _day: []
    )
    result = artifact_tasks.list_to_compose_for_day.run("2026-08-26")

    assert result == []


def test_pin_task_delegates_to_pin_for_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Celery task body is a thin delegation to to_compose_selection.pin_for_tomorrow, wrapping its bool return in a JSON-friendly dict."""
    from app.modules.newspaper.tasks import artifact_tasks

    called = {}

    def _fake_pin(artifact_id: str) -> bool:
        called["artifact_id"] = artifact_id
        return True

    monkeypatch.setattr("app.modules.newspaper.to_compose_selection.pin_for_tomorrow", _fake_pin)
    result = artifact_tasks.pin_artifact_for_tomorrow.run("some-artifact-id")

    assert called["artifact_id"] == "some-artifact-id"
    assert result == {"ok": True, "artifact_id": "some-artifact-id"}


def test_pin_task_reports_false_for_an_unknown_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown artifact_id surfaces as {"ok": False, ...} rather than raising, matching pin_for_tomorrow's own contract for a bad id."""
    from app.modules.newspaper.tasks import artifact_tasks

    monkeypatch.setattr(
        "app.modules.newspaper.to_compose_selection.pin_for_tomorrow", lambda _artifact_id: False
    )
    result = artifact_tasks.pin_artifact_for_tomorrow.run("nope")

    assert result == {"ok": False, "artifact_id": "nope"}


def test_get_artifact_detail_merges_artifact_and_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """The task body merges artifact_store.get_artifact (service_id/url/channel/status) with get_artifact_content (title/content/metadata) into one dict, keyed by the same artifact_id passed in."""
    from datetime import UTC, datetime

    from app.modules.newspaper.artifact_store import Artifact, ArtifactContent
    from app.modules.newspaper.tasks import artifact_tasks

    artifact = Artifact(
        artifact_id="abc-123",
        service_id="svc-a",
        url="https://example.com/post",
        channel="crawler",
        created_at=datetime.now(tz=UTC),
        event_date=None,
        priority=1.5,
        priority_computed_at=None,
        status="pending",
        human_pick_day=None,
    )
    content = ArtifactContent(
        artifact_id="abc-123",
        title="Big protocol update",
        content="Full raw body text goes here.",
        metadata={"display_name": "Some Service"},
    )
    monkeypatch.setattr("app.modules.newspaper.artifact_store.get_artifact", lambda _id: artifact)
    monkeypatch.setattr(
        "app.modules.newspaper.artifact_store.get_artifact_content", lambda _id: content
    )

    result = artifact_tasks.get_artifact_detail.run("abc-123")

    assert result == {
        "artifact_id": "abc-123",
        "title": "Big protocol update",
        "content": "Full raw body text goes here.",
        "metadata": {"display_name": "Some Service"},
        "service_id": "svc-a",
        "url": "https://example.com/post",
        "channel": "crawler",
        "status": "pending",
    }


def test_get_artifact_detail_returns_none_for_unknown_or_malformed_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both get_artifact and get_artifact_content already fail closed to None for an unknown OR malformed id -- the task surfaces that as a plain None, which the admin route turns into a 404."""
    from app.modules.newspaper.tasks import artifact_tasks

    monkeypatch.setattr("app.modules.newspaper.artifact_store.get_artifact", lambda _id: None)
    monkeypatch.setattr(
        "app.modules.newspaper.artifact_store.get_artifact_content", lambda _id: None
    )

    result = artifact_tasks.get_artifact_detail.run("not-a-uuid")

    assert result is None


def test_reset_and_reselect_task_delegates_to_reset_and_reselect_for_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Celery task body is a thin delegation to to_compose_selection.reset_and_reselect_for_day -- the "Redo today's picks" admin action."""
    from app.modules.newspaper.tasks import artifact_tasks

    called = {}

    def _fake_reset_and_reselect(day: str) -> dict[str, object]:
        called["day"] = day
        return {
            "status": "ok",
            "compose_day": day,
            "reset": {"cleared_slots": 1, "reverted_to_pending": ["a"], "skipped": []},
            "selection": {"status": "ok", "compose_day": day},
        }

    monkeypatch.setattr(
        "app.modules.newspaper.to_compose_selection.reset_and_reselect_for_day",
        _fake_reset_and_reselect,
    )
    result = artifact_tasks.reset_and_reselect_to_compose_for_day.run("2026-08-26")

    assert called["day"] == "2026-08-26"
    assert result["reset"]["reverted_to_pending"] == ["a"]
    assert result["selection"]["compose_day"] == "2026-08-26"


def test_all_admin_tasks_registered_under_their_own_names() -> None:
    """Pins the exact task names the backend admin routes dispatch by -- a drifted name here would 404 silently at runtime with no import-time signal."""
    from app.modules.newspaper.tasks import artifact_tasks

    assert artifact_tasks.preview_to_compose_for_day.name == (
        "app.tasks.newspaper.preview_to_compose_for_day"
    )
    assert artifact_tasks.list_to_compose_for_day.name == (
        "app.tasks.newspaper.list_to_compose_for_day"
    )
    assert artifact_tasks.pin_artifact_for_tomorrow.name == (
        "app.tasks.newspaper.pin_artifact_for_tomorrow"
    )
    assert artifact_tasks.get_artifact_detail.name == ("app.tasks.newspaper.get_artifact_detail")
    assert artifact_tasks.reset_and_reselect_to_compose_for_day.name == (
        "app.tasks.newspaper.reset_and_reselect_to_compose_for_day"
    )


def test_artifact_tasks_module_is_imported_by_celery_app() -> None:
    """Both new tasks load only because artifact_tasks itself is registered in celery_app's imports (shared with the sweep task -- see test_artifact_sweep_task.py)."""
    from app.celery_app import celery_app

    assert "app.modules.newspaper.tasks.artifact_tasks" in celery_app.conf.imports
