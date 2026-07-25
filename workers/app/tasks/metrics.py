"""Task registry import: price-metrics Celery tasks."""

from app.modules.metrics.tasks.price_metrics_tasks import collect_price_metrics

__all__ = ["collect_price_metrics"]
