"""Celery task wrapper around run_x_search_weekly_sweep."""

from __future__ import annotations

from app.celery_app import celery_app
from app.modules.newspaper.x_search_sweep import run_x_search_weekly_sweep


@celery_app.task(name="app.tasks.newspaper.sweep_x_search_weekly")
def sweep_x_search_weekly() -> dict[str, object]:
    """Celery beat entrypoint: weekly per-service X (Twitter) search sweep.

    See ``run_x_search_weekly_sweep`` and x_search_sweep.py's module docstring.
    """
    return run_x_search_weekly_sweep()
