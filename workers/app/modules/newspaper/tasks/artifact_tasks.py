"""Celery entries for the editorial-room artifact system (2026-08-25, SHADOW MODE): the daily priority sweep beat, plus two on-demand tasks the new admin preview/pin endpoints dispatch into.

These tasks only ever touch the new `artifacts`/`artifacts_pending`/
`artifact_content`/`to_compose` tables (see artifact_priority.py /
artifact_store.py / to_compose_selection.py) -- none of them interact with
publish_queue, drain_standard_publish_queue, or any other live
compose/publish task. Safe to run regardless of AUTO_COMPOSE_PAUSED or any
other live-pipeline gate, since nothing they read or write is read by
anything live yet.

The backend admin service has no direct import of the workers codebase (per
this repo's usual backend<->workers boundary -- separate services/venvs), so
it reaches these through Celery send_task + a short synchronous .get(),
exactly like the existing admin_interrogate_compose_session /
admin_compose_next routes do for other worker-side reads/writes.
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


@celery_app.task(name="app.tasks.newspaper.preview_to_compose_for_day")
def preview_to_compose_for_day(day: str) -> dict[str, object]:
    """On-demand, read-only: what select_to_compose_for_day(day) currently would pick. Never mutates artifact status or writes to `to_compose` -- see to_compose_selection.preview_to_compose_for_day. Dispatched by the admin shadow-selection dashboard's GET endpoint."""
    from app.modules.newspaper.to_compose_selection import (
        preview_to_compose_for_day as _preview,
    )

    return _preview(day)


@celery_app.task(name="app.tasks.newspaper.pin_artifact_for_tomorrow")
def pin_artifact_for_tomorrow(artifact_id: str) -> dict[str, object]:
    """On-demand write: pin one artifact as tomorrow's human pick -- see to_compose_selection.pin_for_tomorrow. Dispatched by the admin shadow-selection dashboard's "pin for tomorrow" button. Writes only to the new shadow `artifacts`/`artifacts_pending`/`to_compose` tables, which nothing in the live compose/publish path reads yet."""
    from app.modules.newspaper.to_compose_selection import pin_for_tomorrow

    ok = pin_for_tomorrow(artifact_id)
    return {"ok": ok, "artifact_id": artifact_id}
