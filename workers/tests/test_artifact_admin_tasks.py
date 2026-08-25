"""The two on-demand Celery task wrappers artifact_tasks.py adds for the new admin shadow-selection dashboard: preview_to_compose_for_day (read-only) and pin_artifact_for_tomorrow (writes a human pin).

Confirms each task body is a thin delegation to its already-tested
to_compose_selection function (preview_to_compose_for_day / pin_for_tomorrow,
covered directly in test_to_compose_selection.py) and that both are reachable
under their registered task names -- the backend admin routes dispatch by
name via Celery.send_task, so a drifted name would 404 silently at runtime
with no import-time signal.
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


def test_both_admin_tasks_registered_under_their_own_names() -> None:
    """Pins the exact task names the backend admin routes dispatch by -- a drifted name here would 404 silently at runtime with no import-time signal."""
    from app.modules.newspaper.tasks import artifact_tasks

    assert artifact_tasks.preview_to_compose_for_day.name == (
        "app.tasks.newspaper.preview_to_compose_for_day"
    )
    assert artifact_tasks.pin_artifact_for_tomorrow.name == (
        "app.tasks.newspaper.pin_artifact_for_tomorrow"
    )


def test_artifact_tasks_module_is_imported_by_celery_app() -> None:
    """Both new tasks load only because artifact_tasks itself is registered in celery_app's imports (shared with the sweep task -- see test_artifact_sweep_task.py)."""
    from app.celery_app import celery_app

    assert "app.modules.newspaper.tasks.artifact_tasks" in celery_app.conf.imports
