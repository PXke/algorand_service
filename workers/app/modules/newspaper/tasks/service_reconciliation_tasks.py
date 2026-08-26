"""Celery entry for the periodic service-duplication reconciliation sweep (see `app.modules.newspaper.service_reconciliation` for the three scans this runs and exactly what counts as a safe, deterministic auto-action versus a flagged-for-review one).

Mirrors `artifact_tasks.sweep_artifact_priorities`'s shape: a single daily
beat, mostly-read plus a handful of conservative, idempotent writes (index a
legacy domain, merge a clear-cut duplicate, backfill a deterministic
venue_service_id, fold a duplicate pending artifact back to one via the same
concatenation path a real repeat insert already uses). Safe to run
unattended in prod -- nothing ambiguous is ever auto-merged or
auto-backfilled, see that module's own docstrings.
"""

from __future__ import annotations

from app.celery_app import celery_app


@celery_app.task(name="app.tasks.newspaper.reconcile_service_duplicates")
def reconcile_service_duplicates() -> dict[str, object]:
    """Daily beat: run all three service/artifact-duplication reconciliation scans and return their combined findings/actions."""
    from app.modules.newspaper.service_reconciliation import (
        backfill_missing_venue_service_ids,
        reconcile_domain_duplicates,
        reconcile_duplicate_pending_artifacts,
    )

    return {
        "domains": reconcile_domain_duplicates(),
        "venues": backfill_missing_venue_service_ids(),
        "duplicate_artifacts": reconcile_duplicate_pending_artifacts(),
    }
