"""Celery beat entry for the editorial-room artifact priority sweep (2026-08-25, SHADOW MODE).

This task only ever touches the new `artifacts`/`artifacts_pending` tables
(see artifact_priority.sweep_artifact_priorities) -- it has no interaction
with publish_queue, drain_standard_publish_queue, or any other live
compose/publish task. Safe to run on its own beat regardless of
AUTO_COMPOSE_PAUSED or any other live-pipeline gate, since nothing it writes
is read by anything live yet.
"""

from __future__ import annotations

from app.celery_app import celery_app


@celery_app.task(name="app.tasks.newspaper.sweep_artifact_priorities")
def sweep_artifact_priorities() -> dict[str, int]:
    """Daily beat: recompute `priority` for every PENDING artifact. Pure shadow computation -- see artifact_priority.py for the formula."""
    from app.modules.newspaper.artifact_priority import (
        sweep_artifact_priorities as _sweep,
    )

    return _sweep()
