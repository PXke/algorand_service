"""The Celery beat task wrapper (tasks/artifact_tasks.py) and its beat-schedule/import wiring.

Confirms the sweep is registered as its own independent beat entry that
touches nothing on the live publish_queue drain path -- and that the task
body is a thin delegation to artifact_priority.sweep_artifact_priorities
(already covered directly in test_artifact_priority.py).
"""

from __future__ import annotations

import pytest


def test_sweep_task_delegates_to_sweep_artifact_priorities(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Celery task body is a thin delegation to sweep_artifact_priorities."""
    from app.modules.newspaper.tasks import artifact_tasks

    called = {}

    def _fake_sweep() -> dict[str, int]:
        called["ran"] = True
        return {"status": "ok", "swept": 0, "updated": 0}

    monkeypatch.setattr(
        "app.modules.newspaper.artifact_priority.sweep_artifact_priorities", _fake_sweep
    )
    result = artifact_tasks.sweep_artifact_priorities.run()

    assert called.get("ran") is True
    assert result == {"status": "ok", "swept": 0, "updated": 0}


def test_sweep_registered_as_its_own_beat_entry_not_the_drain_tasks() -> None:
    """The sweep is registered under its own task name.

    Pins that it is a distinct, additive beat entry rather than piggybacking
    on any live publish_queue drain task name.
    """
    from app.celery_app import _build_beat_schedule

    schedule = _build_beat_schedule()
    assert "sweep-artifact-priorities" in schedule
    assert schedule["sweep-artifact-priorities"]["task"] == (
        "app.tasks.newspaper.sweep_artifact_priorities"
    )
    # Distinct from (and does not replace) the live selection/drain beats.
    assert schedule["drain-standard-publish-queue"]["task"] == (
        "app.tasks.newspaper.drain_standard_publish_queue"
    )


def test_artifact_tasks_module_is_imported_by_celery_app() -> None:
    """The new task module is registered in celery_app's imports so its beat entry actually loads."""
    from app.celery_app import celery_app

    assert "app.modules.newspaper.tasks.artifact_tasks" in celery_app.conf.imports
