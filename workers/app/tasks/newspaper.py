"""Task registry import: newspaper (compose/publish) Celery tasks."""

from app.modules.newspaper.tasks.distribution_tasks import distribute_article
from app.modules.newspaper.tasks.mistral_diff_tasks import check_and_publish_mistral_on_diff
from app.modules.newspaper.tasks.price_analysis_tasks import (
    publish_weekly_digest,
    publish_weekly_price_analysis,
)
from app.modules.newspaper.tasks.publish_tasks import (
    apply_recomposed_article,
    publish_from_chain_event,
    recompose_published,
    recompose_review,
)
from app.modules.newspaper.tasks.queue_drain_tasks import (
    drain_breaking_publish_queue,
    drain_standard_publish_queue,
    expire_stale_queue_items,
)

__all__ = [
    "apply_recomposed_article",
    "check_and_publish_mistral_on_diff",
    "distribute_article",
    "drain_breaking_publish_queue",
    "drain_standard_publish_queue",
    "expire_stale_queue_items",
    "publish_from_chain_event",
    "publish_weekly_digest",
    "publish_weekly_price_analysis",
    "recompose_published",
    "recompose_review",
]
