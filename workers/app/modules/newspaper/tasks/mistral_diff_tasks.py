"""Celery task wrapper around run_mistral_diff_check."""

from __future__ import annotations

from app.celery_app import celery_app
from app.modules.newspaper.mistral_diff_check import run_mistral_diff_check


@celery_app.task(name="app.tasks.newspaper.check_and_publish_mistral_on_diff")
def check_and_publish_mistral_on_diff() -> dict[str, object]:
    """Celery beat entrypoint: diff-check all scrape sources and publish via Mistral.

    See ``run_mistral_diff_check`` and docs/modules/ai-mistral-connector.md.
    """
    return run_mistral_diff_check()
