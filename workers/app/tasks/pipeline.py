"""Task registry import: pipeline (diffing) Celery tasks."""

from app.modules.pipeline.tasks.pipeline_tasks import diff_snapshot

__all__ = ["diff_snapshot"]
