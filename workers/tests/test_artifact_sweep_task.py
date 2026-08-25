"""The Celery beat task wrapper (tasks/artifact_tasks.py) and its beat-schedule/import wiring.

Confirms the sweep is registered as its own independent beat entry, distinct
from (though feeding into) drain_to_compose's daily selection -- and that
the task body is a thin delegation to artifact_priority.sweep_artifact_priorities
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
    on the live selection/compose-trigger task names.
    """
    from app.celery_app import _build_beat_schedule

    schedule = _build_beat_schedule()
    assert "sweep-artifact-priorities" in schedule
    assert schedule["sweep-artifact-priorities"]["task"] == (
        "app.tasks.newspaper.sweep_artifact_priorities"
    )
    # Distinct from (and does not replace) the live selection/drain beats.
    assert schedule["drain-to-compose"]["task"] == "app.tasks.newspaper.drain_to_compose"
    assert schedule["select-to-compose-for-today"]["task"] == (
        "app.tasks.newspaper.select_to_compose_for_today"
    )


def test_artifact_tasks_module_is_imported_by_celery_app() -> None:
    """The new task module is registered in celery_app's imports so its beat entry actually loads."""
    from app.celery_app import celery_app

    assert "app.modules.newspaper.tasks.artifact_tasks" in celery_app.conf.imports
