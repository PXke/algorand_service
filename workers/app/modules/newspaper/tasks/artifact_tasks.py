"""Celery entries for the editorial-room artifact system: the daily priority sweep beat, plus four on-demand tasks the admin preview/selected/pin/content endpoints dispatch into.

These tasks only ever touch the `artifacts`/`artifacts_pending`/
`artifact_content`/`to_compose` tables (see artifact_priority.py /
artifact_store.py / to_compose_selection.py) directly -- they don't call
into queue_drain_tasks.py themselves. Safe to run regardless of
AUTO_COMPOSE_PAUSED or any other live-pipeline gate: scoring/preview/pin are
cheap pure computation and Cassandra writes, not a compose spend, and should
stay fresh even while composing itself is paused.

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


@celery_app.task(name="app.tasks.newspaper.list_to_compose_for_day")
def list_to_compose_for_day(day: str) -> list[dict[str, object]]:
    """On-demand, read-only: the REAL persisted `to_compose` lineup for `day` -- what select_to_compose_for_day(day) actually picked the last time the daily beat ran, not a forecast. See to_compose_selection.list_to_compose_for_day. Dispatched by the admin dashboard's "selected for tomorrow" GET endpoint. Empty until select_to_compose_for_today_task has fired at least once for `day`."""
    from app.modules.newspaper.to_compose_selection import (
        list_to_compose_for_day as _list_selected,
    )

    return _list_selected(day)


@celery_app.task(name="app.tasks.newspaper.pin_artifact_for_tomorrow")
def pin_artifact_for_tomorrow(artifact_id: str) -> dict[str, object]:
    """On-demand write: pin one artifact as tomorrow's human pick -- see to_compose_selection.pin_for_tomorrow. Dispatched by the admin shadow-selection dashboard's "pin for tomorrow" button. Writes only to the new shadow `artifacts`/`artifacts_pending`/`to_compose` tables, which nothing in the live compose/publish path reads yet."""
    from app.modules.newspaper.to_compose_selection import pin_for_tomorrow

    ok = pin_for_tomorrow(artifact_id)
    return {"ok": ok, "artifact_id": artifact_id}


@celery_app.task(name="app.tasks.newspaper.get_artifact_detail")
def get_artifact_detail(artifact_id: str) -> dict[str, object] | None:
    """On-demand, read-only: one artifact's full title/content/url/metadata -- what would actually get fed to the writer/composer -- fetched only when an admin expands a Queue-tab row to inspect it, never on the list/preview poll (which stays title-only). See artifact_store.get_artifact / get_artifact_content, a point read against `artifacts` + `artifact_content` by primary key.

    Returns None for an unknown OR malformed artifact_id (both get_artifact
    and get_artifact_content already fail closed to None on a bad uuid) --
    the admin route turns that into a clean 404.
    """
    from app.modules.newspaper.artifact_store import get_artifact, get_artifact_content

    artifact = get_artifact(artifact_id)
    content = get_artifact_content(artifact_id)
    if artifact is None and content is None:
        return None
    return {
        "artifact_id": artifact_id,
        "title": content.title if content else "",
        "content": content.content if content else "",
        "metadata": content.metadata if content else {},
        "service_id": artifact.service_id if artifact else None,
        "url": artifact.url if artifact else None,
        "channel": artifact.channel if artifact else "",
        "status": artifact.status if artifact else "",
    }
