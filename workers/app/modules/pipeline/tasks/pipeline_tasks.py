from __future__ import annotations

from app.celery_app import celery_app
from app.modules.pipeline.core.diffing import build_text_diff


@celery_app.task(name="app.tasks.pipeline.diff_snapshot")
def diff_snapshot(previous_text: str, current_text: str) -> dict[str, str]:
    diff = build_text_diff(previous=previous_text, current=current_text)
    return {"diff": diff, "has_changes": str(bool(diff)).lower()}
