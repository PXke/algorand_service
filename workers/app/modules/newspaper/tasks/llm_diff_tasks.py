"""Celery task wrapper around run_llm_diff_check."""

from __future__ import annotations

from app.celery_app import celery_app
from app.modules.newspaper.llm_diff_check import run_llm_diff_check

# NOTE: the registered task `name=` is deliberately kept at its historical
# "...check_and_publish_mistral_on_diff" string even though the Python
# function below is renamed -- backend/app/modules/admin/api/routes.py's
# admin_compose_next() triggers this exact task by string via a
# cross-service `Celery(...).send_task("app.tasks.newspaper.
# check_and_publish_mistral_on_diff", ...)` call (out of scope for this
# workers-only rename pass). Celery allows the registered name and the
# Python function name to differ, so this is safe.


@celery_app.task(name="app.tasks.newspaper.check_and_publish_mistral_on_diff")
def check_and_publish_llm_on_diff() -> dict[str, object]:
    """Celery beat entrypoint: diff-check all scrape sources and publish via the LLM writer.

    See ``run_llm_diff_check`` and docs/modules/ai-mistral-connector.md.
    """
    return run_llm_diff_check()
