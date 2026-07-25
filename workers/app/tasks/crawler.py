"""Task registry import: crawler Celery tasks."""

from app.modules.crawler.tasks.url_queue_tasks import (
    drain_url_queue,
    retrain_publish_classifier_task,
)

__all__ = ["drain_url_queue", "retrain_publish_classifier_task"]
