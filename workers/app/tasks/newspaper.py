"""Task registry import: newspaper (compose/publish) Celery tasks."""

from app.modules.ai.interrogate import interrogate_compose_session_task
from app.modules.newspaper.tasks.analytics_flush_tasks import flush_pending_analytics_task
from app.modules.newspaper.tasks.distribution_tasks import distribute_article
from app.modules.newspaper.tasks.llm_diff_tasks import check_and_publish_llm_on_diff
from app.modules.newspaper.tasks.price_analysis_tasks import (
    publish_weekly_digest,
    publish_weekly_price_analysis,
)
from app.modules.newspaper.tasks.publish_tasks import (
    apply_recomposed_article,
    publish_from_chain_event,
    recompose_published,
    recompose_review,
    recompose_session_service,
)
from app.modules.newspaper.tasks.queue_drain_tasks import (
    compose_artifact_now,
    drain_to_compose,
    select_to_compose_for_today_task,
)
from app.modules.newspaper.tasks.view_count_tasks import flush_pending_views_task
from app.modules.newspaper.tasks.x_search_sweep_tasks import sweep_x_search_weekly

__all__ = [
    "apply_recomposed_article",
    "check_and_publish_llm_on_diff",
    "compose_artifact_now",
    "distribute_article",
    "drain_to_compose",
    "flush_pending_analytics_task",
    "flush_pending_views_task",
    "interrogate_compose_session_task",
    "publish_from_chain_event",
    "publish_weekly_digest",
    "publish_weekly_price_analysis",
    "recompose_published",
    "recompose_review",
    "recompose_session_service",
    "select_to_compose_for_today_task",
    "sweep_x_search_weekly",
]
