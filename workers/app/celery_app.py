import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from app.modules.scraper.crawler_registry import is_crawler_enabled
from app.modules.scraper.crawler_types import CrawlerType

_broker = os.getenv("REDIS_BROKER_URL", "redis://localhost:6379/1")
_result_backend = os.getenv("REDIS_RESULT_URL", "redis://localhost:6379/2")

celery_app = Celery(
    "algorand_platform_workers",
    broker=_broker,
    backend=_result_backend,
)

# Kill hung tasks instead of wedging a worker slot forever. Sized generously: a
# single article compose is one-at-a-time (rate-limited to ~0.42 rps) and may run
# up to MISTRAL_MAX_TOOL_ROUNDS agentic rounds, so a healthy compose can legitimately
# take several minutes. The hard limit leaves 60s of grace so a task that catches
# SoftTimeLimitExceeded can return partial progress before the kill.
celery_app.conf.task_soft_time_limit = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "1800"))
celery_app.conf.task_time_limit = int(os.getenv("CELERY_TASK_TIME_LIMIT", "1860"))


@worker_process_init.connect
def _reset_cassandra_session(**_kwargs):
    """cassandra-driver sessions don't survive prefork: the driver's IO event
    loop thread stays in the parent, so a forked child inheriting the cached
    session blocks forever on its first query. Drop the cache so every child
    opens its own connection."""
    from app.core.cassandra import get_cassandra_session

    get_cassandra_session.cache_clear()


celery_app.conf.task_default_queue = "default"
celery_app.conf.task_routes = {
    "app.tasks.scrape.*": {"queue": "scrape"},
    "app.tasks.pipeline.*": {"queue": "pipeline"},
    "app.tasks.security.*": {"queue": "security"},
    "app.tasks.chain_tail.*": {"queue": "chain"},
    "app.tasks.newspaper.*": {"queue": "pipeline"},
    "app.tasks.crawler.*": {"queue": "scrape"},
    "app.tasks.search.*": {"queue": "pipeline"},
    "app.tasks.metrics.*": {"queue": "pipeline"},
    "app.tasks.gatekeeper.*": {"queue": "pipeline"},
}
celery_app.conf.imports = (
    "app.modules.newspaper.tasks.mail_poll_tasks",
    "app.modules.crawler.tasks.url_queue_tasks",
    "app.tasks.scrape",
    "app.tasks.crawler",
    "app.tasks.pipeline",
    "app.tasks.security",
    "app.tasks.chain_tail",
    "app.tasks.newspaper",
    "app.tasks.search",
    "app.tasks.metrics",
    "app.tasks.gatekeeper",
)
def _build_beat_schedule() -> dict:
    schedule = {}
    if is_crawler_enabled(CrawlerType.CHAIN):
        schedule["chain-tail-process-rounds"] = {
            "task": "app.tasks.chain_tail.process_new_rounds",
            "schedule": float(os.getenv("CHAIN_TAIL_POLL_SECONDS", "60")),
        }

    if is_crawler_enabled(CrawlerType.YOUTUBE):
        schedule["youtube-poll-sources"] = {
            "task": "app.tasks.scrape.poll_youtube_sources",
            "schedule": float(os.getenv("YOUTUBE_POLL_SECONDS", "3600")),
        }

    if is_crawler_enabled(CrawlerType.BLUESKY):
        schedule["bluesky-poll-sources"] = {
            "task": "app.tasks.scrape.poll_bluesky_sources",
            "schedule": float(os.getenv("BLUESKY_POLL_SECONDS", "3600")),
        }
    # Beats are a slow SAFETY-NET heartbeat: the real work is triggered on demand
    # by admin actions (approving an article fires drain_standard_publish_queue;
    # approving a domain fires drain_url_queue + fetch_source). So these can be
    # spaced way out — workers stay idle until you accept something.
    schedule["drain-url-queue"] = {
        "task": "app.tasks.crawler.drain_url_queue",
        # Default ~1 page / 10s: gentle on any single domain, yet clears a new
        # domain's 20-page initial harvest in ~3 min (vs the old 5/hour).
        "schedule": float(os.getenv("URL_QUEUE_DRAIN_SECONDS", "10")),
        "kwargs": {"max_items": int(os.getenv("URL_QUEUE_DRAIN_BATCH", "1"))},
    }
    schedule["retrain-publish-classifier"] = {
        "task": "app.tasks.crawler.retrain_publish_classifier",
        "schedule": crontab(
            minute=int(os.getenv("CLASSIFIER_RETRAIN_CRON_MINUTE", "30")),
            hour=int(os.getenv("CLASSIFIER_RETRAIN_CRON_HOUR", "3")),
        ),
    }
    schedule["mistral-diff-publish"] = {
        "task": "app.tasks.newspaper.check_and_publish_mistral_on_diff",
        "schedule": float(os.getenv("MISTRAL_DIFF_POLL_SECONDS", "600")),
    }
    schedule["weekly-price-analysis"] = {
        "task": "app.tasks.newspaper.publish_weekly_price_analysis",
        "schedule": crontab(
            minute=int(os.getenv("PRICE_ANALYSIS_CRON_MINUTE", "0")),
            hour=int(os.getenv("PRICE_ANALYSIS_CRON_HOUR", "9")),
            day_of_week=os.getenv("PRICE_ANALYSIS_CRON_DOW", "mon"),
        ),
    }
    if is_crawler_enabled(CrawlerType.METRICS):
        schedule["collect-price-metrics"] = {
            "task": "app.tasks.metrics.collect_price_metrics",
            "schedule": float(os.getenv("PRICE_METRICS_POLL_SECONDS", "3600")),
        }
    schedule["ensure-review-ready"] = {
        "task": "app.tasks.newspaper.ensure_review_ready",
        "schedule": float(os.getenv("ENSURE_REVIEW_READY_SECONDS", "3600")),
    }
    schedule["drain-approved-feed-queue"] = {
        "task": "app.tasks.newspaper.drain_approved_feed_queue",
        "schedule": float(os.getenv("APPROVED_FEED_DRAIN_SECONDS", "3600")),
    }
    schedule["drain-standard-publish-queue"] = {
        "task": "app.tasks.newspaper.drain_standard_publish_queue",
        "schedule": float(os.getenv("PUBLISH_QUEUE_DRAIN_SECONDS", "3600")),
    }
    schedule["drain-breaking-publish-queue"] = {
        "task": "app.tasks.newspaper.drain_breaking_publish_queue",
        "schedule": float(os.getenv("PUBLISH_BREAKING_DRAIN_SECONDS", "300")),
    }
    schedule["expire-stale-queue-items"] = {
        "task": "app.tasks.newspaper.expire_stale_queue_items",
        "schedule": float(os.getenv("PUBLISH_QUEUE_MAINTENANCE_SECONDS", "3600")),
    }
    schedule["reap-stale-compose-sessions"] = {
        "task": "app.tasks.newspaper.reap_stale_compose_sessions",
        "schedule": float(os.getenv("COMPOSE_SESSION_REAP_SECONDS", "3600")),
    }
    if is_crawler_enabled(CrawlerType.MAIL):
        schedule["mail-poll-inbox"] = {
            "task": "app.tasks.newspaper.poll_mail_inbox",
            "schedule": float(os.getenv("MAIL_POLL_SECONDS", "300")),
        }
    schedule["scan-editorial-brief-schedule"] = {
        "task": "app.tasks.newspaper.scan_editorial_brief_schedule",
        "schedule": float(os.getenv("EDITORIAL_BRIEF_SCAN_SECONDS", "3600")),
    }
    return schedule


celery_app.conf.beat_schedule = _build_beat_schedule()
celery_app.conf.task_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.result_serializer = "json"

# Task duration logging (workers/app/core/task_timing.py)
import app.core.task_timing  # noqa: E402, F401


def _init_bugsnag() -> None:
    """Bugsnag for Celery: task-failure signal + ERROR-log handler."""
    try:
        key = os.getenv("BUGSNAG_API_KEY", "b83be2212bf6cbca2e5abc3510f91210").strip()
        if not key:
            return
        import logging

        import bugsnag
        from bugsnag.celery import connect_failure_handler
        from bugsnag.handlers import BugsnagHandler

        bugsnag.configure(
            api_key=key,
            release_stage=os.getenv("BUGSNAG_RELEASE_STAGE", os.getenv("APP_ENV", "prod")),
            auto_capture_sessions=True,
        )
        connect_failure_handler()
        handler = BugsnagHandler()
        handler.setLevel(logging.ERROR)
        logging.getLogger().addHandler(handler)
    except Exception:
        logging.getLogger(__name__).warning(
            "bugsnag setup failed; error reporting disabled", exc_info=True
        )


_init_bugsnag()
