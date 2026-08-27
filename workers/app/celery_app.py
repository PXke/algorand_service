"""Celery app wiring: broker/backend config, beat schedule, Bugsnag, and prefork-safety hooks."""

import os
from pathlib import Path

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
# up to LLM_MAX_TOOL_ROUNDS agentic rounds, so a healthy compose can legitimately
# take several minutes. The hard limit leaves 60s of grace so a task that catches
# SoftTimeLimitExceeded can return partial progress before the kill.
celery_app.conf.task_soft_time_limit = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "1800"))
celery_app.conf.task_time_limit = int(os.getenv("CELERY_TASK_TIME_LIMIT", "1860"))


@worker_process_init.connect
def _reset_cassandra_session(**_kwargs: object) -> None:
    """cassandra-driver sessions don't survive prefork: the driver's IO event loop thread stays in the parent, so a forked child inheriting the cached session blocks forever on its first query. Drop the cache so every child opens its own connection."""
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
    # Exact-name entry, checked by Celery's router BEFORE the
    # "app.tasks.newspaper.*" glob above regardless of dict order (an exact
    # match always wins over a pattern match) -- pulled off the shared
    # pipeline queue onto its own, consumed only by the dedicated
    # algorand-platform-celery-translate worker (-Q translate,
    # --concurrency=1). It needs isolation the rest of "newspaper.*" doesn't:
    # a multi-hour batch that loads a multi-GB local model must never share
    # a worker slot with anything else, and must never be picked up by the
    # general pool under load.
    "app.tasks.newspaper.translate_article_batch": {"queue": "translate"},
}
celery_app.conf.imports = (
    "app.modules.newspaper.tasks.mail_poll_tasks",
    "app.modules.newspaper.tasks.artifact_tasks",
    "app.modules.newspaper.tasks.service_reconciliation_tasks",
    "app.modules.crawler.tasks.url_queue_tasks",
    "app.tasks.scrape",
    "app.tasks.crawler",
    "app.tasks.pipeline",
    "app.tasks.security",
    "app.tasks.chain_tail",
    "app.tasks.newspaper",
    "app.tasks.search",
    "app.tasks.metrics",
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
    # by admin actions (approving/rejecting a review fires drain_to_compose;
    # approving a domain fires drain_url_queue + fetch_source). So these can be
    # spaced way out — workers stay idle until you accept something.
    schedule["drain-url-queue"] = {
        "task": "app.tasks.crawler.drain_url_queue",
        # Default 10 pages / 10s: clears a new domain's 20-page initial harvest
        # in one tick, and a large backlog (e.g. an admin-approval bulk
        # backfill) in minutes instead of hours (bumped from 1/tick 2026-07-21).
        "schedule": float(os.getenv("URL_QUEUE_DRAIN_SECONDS", "10")),
        "kwargs": {"max_items": int(os.getenv("URL_QUEUE_DRAIN_BATCH", "10"))},
    }
    schedule["retrain-publish-classifier"] = {
        "task": "app.tasks.crawler.retrain_publish_classifier",
        "schedule": crontab(
            minute=int(os.getenv("CLASSIFIER_RETRAIN_CRON_MINUTE", "30")),
            hour=int(os.getenv("CLASSIFIER_RETRAIN_CRON_HOUR", "3")),
        ),
    }
    schedule["llm-diff-publish"] = {
        # NOTE: this registered task name is deliberately NOT renamed to
        # match check_and_publish_llm_on_diff's new Python name below --
        # backend/app/modules/admin/api/routes.py's admin_compose_next()
        # triggers this exact task by string via a cross-service
        # `Celery(...).send_task("app.tasks.newspaper.check_and_publish_
        # mistral_on_diff", ...)` call. Renaming the wire-level task name
        # here without updating backend (out of scope for this pass) would
        # silently break that admin "compose next" button in prod.
        "task": "app.tasks.newspaper.check_and_publish_mistral_on_diff",
        "schedule": float(os.getenv("MISTRAL_DIFF_POLL_SECONDS", "600")),
    }
    # Weekly digest retired 2026-08-18 (owner call) -- opt back in with
    # WEEKLY_DIGEST_ENABLED=1 if it's ever wanted again.
    if os.getenv("WEEKLY_DIGEST_ENABLED", "0") == "1":
        schedule["weekly-price-analysis"] = {
            "task": "app.tasks.newspaper.publish_weekly_price_analysis",
            "schedule": crontab(
                minute=int(os.getenv("PRICE_ANALYSIS_CRON_MINUTE", "0")),
                # Default moved off DeepSeek peak hours (2026-08-15): 9 sat inside
                # the 06:00-10:00 UTC peak window. The compose itself is also
                # gated by article_composer's off-peak check regardless of this
                # cron hour (see peak_hours.py) -- this default just avoids
                # scheduling the one hour-configurable LLM task to immediately
                # collide with peak on every run.
                hour=int(os.getenv("PRICE_ANALYSIS_CRON_HOUR", "11")),
                day_of_week=os.getenv("PRICE_ANALYSIS_CRON_DOW", "mon"),
            ),
        }
    # search_x's data source (redesigned 2026-08-25 from a live per-compose
    # call): gated on X_SEARCH_ENABLED the same way WEEKLY_DIGEST_ENABLED
    # above gates its own beat entry -- X_SEARCH_ENABLED is the feature's
    # master kill switch (see config.py), now controlling this weekly sweep
    # rather than a live call path. run_x_search_weekly_sweep() re-checks
    # the same flag itself, so a manual/admin trigger of the task also stays
    # a no-op when the feature is off.
    if os.getenv("X_SEARCH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        schedule["x-search-weekly-sweep"] = {
            "task": "app.tasks.newspaper.sweep_x_search_weekly",
            "schedule": crontab(
                minute=int(os.getenv("X_SEARCH_SWEEP_CRON_MINUTE", "0")),
                hour=int(os.getenv("X_SEARCH_SWEEP_CRON_HOUR", "8")),
                day_of_week=os.getenv("X_SEARCH_SWEEP_CRON_DOW", "sun"),
            ),
        }
    if is_crawler_enabled(CrawlerType.METRICS):
        schedule["collect-price-metrics"] = {
            "task": "app.tasks.metrics.collect_price_metrics",
            "schedule": float(os.getenv("PRICE_METRICS_POLL_SECONDS", "3600")),
        }
    # ensure_review_ready was retired 2026-08-25 (folded into drain_to_compose,
    # which now composes eligible review-bound to_compose slots on every one
    # of its own runs -- see queue_drain_tasks.py's module docstring).
    #
    # drain_approved_feed_queue's pending_feed_queue release was folded into
    # drain_standard_publish_queue (2026-07-14) — they already shared one
    # pacing gate/budget, so a separate task+beat entry was an avoidable
    # extra moving part that most cycles did nothing anyway. The task itself
    # is kept registered (queue_drain_tasks.py) for manual/debug triggers.
    # drain_to_compose (its 2026-08-25 successor) inherited this same fold-in.
    schedule["sync-ecosystem-directories"] = {
        "task": "app.tasks.crawler.sync_ecosystem_directories",
        "schedule": float(os.getenv("ECOSYSTEM_SYNC_SECONDS", "86400")),
    }
    schedule["discover-from-mentions"] = {
        "task": "app.tasks.crawler.discover_from_mentions",
        "schedule": float(os.getenv("MENTION_DISCOVERY_SECONDS", "86400")),
    }
    schedule["poll-forum-topics"] = {
        "task": "app.tasks.scrape.poll_forum_topics",
        "schedule": float(os.getenv("FORUM_POLL_SECONDS", "1800")),
    }
    schedule["poll-xgov-proposals"] = {
        "task": "app.tasks.chain_tail.poll_xgov_proposals",
        "schedule": float(os.getenv("XGOV_POLL_SECONDS", "3600")),
    }
    schedule["reevaluate-pending-domains"] = {
        "task": "app.tasks.crawler.reevaluate_pending_domains",
        "schedule": float(os.getenv("PENDING_REEVALUATE_SECONDS", "86400")),
    }
    # One-time gray-zone reconciliation (2026-08-26 audit, see
    # gray_zone_reconciliation.py's module docstring): companion to
    # reevaluate-pending-domains above, but for domains already
    # frontier_status="approved" whose content_relevance never actually
    # cleared FRONTIER_CONTENT_PROMOTE_SCORE — a bucket reevaluate-pending-
    # domains's own pending-only scan never touches. OFF by default (same
    # opt-in shape as scan-editorial-brief-schedule below) and deliberately
    # small/slow when on: unlike every other read-mostly sweep on this
    # schedule, each domain this dispatches fires a REAL deep_classify_domain
    # crawl on the scrape queue, so it must stay a small throttled trickle —
    # the resource-contention incident this whole design avoids repeating was
    # exactly a big batch of classify_pending_domains chunks fired at once,
    # saturating the concurrency=4 scrape worker pool and starving unrelated
    # admin/routine tasks.
    if os.getenv("FRONTIER_GRAY_ZONE_RECLASSIFY_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        schedule["reclassify-gray-zone-domains"] = {
            "task": "app.tasks.crawler.reclassify_gray_zone_domains",
            "schedule": float(os.getenv("FRONTIER_GRAY_ZONE_RECLASSIFY_SECONDS", "1800")),
            "kwargs": {"limit": int(os.getenv("FRONTIER_GRAY_ZONE_RECLASSIFY_LIMIT", "5"))},
        }
    # Editorial-room compose trigger (2026-08-25): replaces
    # drain_standard_publish_queue as the live selection/compose mechanism --
    # see queue_drain_tasks.py's module docstring for the full picture. Same
    # env var and default interval as the task it replaces (PUBLISH_QUEUE_DRAIN_SECONDS
    # kept, not renamed, so an existing prod env override carries forward
    # unchanged). The BREAKING fast path (drain-breaking-publish-queue, its
    # own ~5min beat) was removed entirely, not folded in here — owner's
    # call, "it is a concept that didn't work well" (see PublishTier's
    # docstring and the deleted breaking_credibility.py).
    schedule["drain-to-compose"] = {
        "task": "app.tasks.newspaper.drain_to_compose",
        "schedule": float(os.getenv("PUBLISH_QUEUE_DRAIN_SECONDS", "3600")),
    }
    # Once-daily: picks the day's to_compose slate (human pin + N-1 platform
    # picks). Runs early UTC so a "pin for tomorrow" set any time the day
    # before is captured before drain-to-compose's first run of the day.
    # drain_to_compose self-heals via _ensure_today_selected if this beat is
    # ever late/missed, so the exact hour isn't precision-critical.
    schedule["select-to-compose-for-today"] = {
        "task": "app.tasks.newspaper.select_to_compose_for_today",
        "schedule": crontab(
            minute=int(os.getenv("TO_COMPOSE_SELECT_CRON_MINUTE", "5")),
            hour=int(os.getenv("TO_COMPOSE_SELECT_CRON_HOUR", "0")),
        ),
    }
    schedule["reap-stale-compose-sessions"] = {
        "task": "app.tasks.newspaper.reap_stale_compose_sessions",
        "schedule": float(os.getenv("COMPOSE_SESSION_REAP_SECONDS", "3600")),
    }
    schedule["reap-stale-translation-sessions"] = {
        "task": "app.tasks.newspaper.reap_stale_translation_sessions",
        "schedule": float(os.getenv("TRANSLATION_SESSION_REAP_SECONDS", "3600")),
    }
    # OS-level companion to the two DB-row reapers above (root-caused
    # 2026-08-26, see browser_reaper.py's module docstring): a forceful
    # worker kill -- hard time_limit SIGKILL, a deploy's SIGQUIT cold
    # shutdown, an admin revoke(terminate=True) -- never signals the
    # Playwright driver/Chromium process at all, so it survives as an
    # orphan burning CPU/RAM until something kills it. Frequent (5min
    # default) and cheap (one `ps` call); the min-age floor inside the
    # reaper itself is what keeps it from ever touching a live session.
    schedule["reap-orphaned-browser-processes"] = {
        "task": "app.tasks.newspaper.reap_orphaned_browser_processes",
        "schedule": float(os.getenv("BROWSER_REAP_SECONDS", "300")),
    }
    # Root-caused 2026-08-26 (see to_compose_selection.
    # find_stale_selected_artifacts's own docstring): select_to_compose_for_day
    # flips a picked artifact PENDING -> SELECTED immediately, for both the
    # human pick and every platform pick alike, but drain_to_compose only
    # ever composes TODAY's slate -- a slot still SELECTED when its day
    # rolls over is invisible to every future day's selection and drain run,
    # permanently stranded with no recovery path otherwise. Hourly and cheap
    # (to_compose holds a handful of rows per day, ever) -- found two real
    # already-stranded platform picks live before this existed.
    schedule["reclaim-stale-selected-artifacts"] = {
        "task": "app.tasks.newspaper.reclaim_stale_selected_artifacts",
        "schedule": float(os.getenv("STALE_SELECTION_REAP_SECONDS", "3600")),
    }
    # Root-caused 2026-08-27 (arima.io): a pending artifact's source can go
    # dark -- domain registration expires, page becomes a registrar parking
    # template -- while it sits unselected, with nothing re-checking before
    # it could later be selected/composed as if the project were still
    # current (see source_liveness.py's module docstring; the pre-compose
    # gate in queue_drain_tasks.py catches the narrower AFTER-selection
    # window, this catches it before that). Deliberately slow and small
    # (15/run, hourly default): each check is a real network fetch with an
    # 8s timeout, and this box also runs other latency-sensitive services --
    # a slow trickle across many runs, never a one-shot sweep.
    schedule["discard-dead-pending-sources"] = {
        "task": "app.tasks.newspaper.discard_dead_pending_sources",
        "schedule": float(os.getenv("DEAD_SOURCE_SWEEP_SECONDS", "3600")),
    }
    # Editorial-room artifacts: recomputes priority for every PENDING
    # artifact once a day, feeding drain-to-compose's daily selection above.
    # Runs unconditionally (no AUTO_COMPOSE_PAUSED-style gate) -- scoring is
    # cheap pure computation, not a compose spend, so it should stay fresh
    # even while composing itself is paused.
    schedule["sweep-artifact-priorities"] = {
        "task": "app.tasks.newspaper.sweep_artifact_priorities",
        "schedule": float(os.getenv("ARTIFACT_PRIORITY_SWEEP_SECONDS", "86400")),
    }
    # Ongoing automated detection for the two service-duplication bug
    # classes found in the 2026-08-2x new-service-lane audit (see
    # service_reconciliation.py's own docstring): literal domain-registry
    # duplicates (bug class 1 -- a legacy/seeded service_registry row never
    # indexed into service_sources) and per-item ingest lanes with no venue
    # concept (bug class 2 -- an artifact missing venue_service_id). Same
    # daily cadence as sweep-artifact-priorities -- cheap, mostly-read scan;
    # only fires deterministic/conservative auto-actions (index an unclaimed
    # domain, merge a clear-cut duplicate, backfill an unambiguous
    # venue_service_id), everything else is flagged via a warning log for
    # manual review, never auto-merged/auto-backfilled.
    schedule["reconcile-service-duplicates"] = {
        "task": "app.tasks.newspaper.reconcile_service_duplicates",
        "schedule": float(os.getenv("SERVICE_RECONCILE_SWEEP_SECONDS", "86400")),
    }
    # Drains backend's Redis-buffered per-article view increments into the
    # article_view_counts Cassandra counter (2026-08-25, replacing a direct
    # Cassandra write on every article page view — counter columns are their
    # own write path and don't batch). 10 minutes: nothing reads this counter
    # for anything sub-10-minute (hot_feed's velocity ranking floors article
    # age at 6h), so this is a vanity/ranking metric that tolerates a short,
    # self-correcting lag.
    schedule["flush-pending-view-counts"] = {
        "task": "app.tasks.newspaper.flush_pending_views",
        "schedule": float(os.getenv("VIEW_COUNT_FLUSH_SECONDS", "600")),
    }
    # Drains backend's Redis-buffered pageview-analytics deltas (geo/campaign/
    # hour/language/referrer_path/referrer_url only -- everything the
    # UA-repeat-offender clawback reads stays synchronous, see
    # backend/app/modules/seo/analytics_store.py's note above
    # _write_pageview_counters) into their Cassandra counters (2026-08-25).
    # Same 10-minute cadence as flush-pending-view-counts: these feed an
    # admin-only breakdown dashboard, not anything real-time.
    schedule["flush-pending-analytics"] = {
        "task": "app.tasks.newspaper.flush_pending_analytics",
        "schedule": float(os.getenv("ANALYTICS_FLUSH_SECONDS", "600")),
    }
    # Self-heals index_article.delay() misses: that task fires once at publish
    # time with no retry, so a transient Typesense hiccup silently drops an
    # article from search forever (found 2026-08-02: a live, feed-listed
    # article missing from every result). Idempotent upsert, safe to re-run.
    schedule["reindex-articles"] = {
        "task": "app.tasks.search.reindex_articles",
        "schedule": float(os.getenv("ARTICLE_REINDEX_SECONDS", "86400")),
        "kwargs": {"limit": int(os.getenv("ARTICLE_REINDEX_LIMIT", "1000"))},
    }
    if is_crawler_enabled(CrawlerType.MAIL):
        schedule["mail-poll-inbox"] = {
            "task": "app.tasks.newspaper.poll_mail_inbox",
            "schedule": float(os.getenv("MAIL_POLL_SECONDS", "300")),
        }
    # Editorial-brief recurrence (auto-assign never-run briefs + cadence
    # refresh) is OFF by default: it silently regenerated standing briefs with
    # no operator action (a 30-day brief re-ran and republished on its own,
    # 2026-07-19). Briefs now only compose when explicitly triggered via the
    # admin API. Set EDITORIAL_BRIEF_SCAN_ENABLED=true in workers.env to restore
    # the recurring beat.
    if os.getenv("EDITORIAL_BRIEF_SCAN_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        schedule["scan-editorial-brief-schedule"] = {
            "task": "app.tasks.newspaper.scan_editorial_brief_schedule",
            "schedule": float(os.getenv("EDITORIAL_BRIEF_SCAN_SECONDS", "3600")),
        }
    return schedule


# PersistentScheduler's on-disk last-run-at bookkeeping defaults to a path
# relative to CWD, which is the per-release directory under our deploy layout
# (releases/current/workers). Every deploy replaces that directory, so beat
# would otherwise "forget" every task's last run on each redeploy and re-fire
# the whole schedule immediately regardless of its configured interval. Put it
# next to workers/.env instead, which deploy.sh symlinks into the shared
# directory that survives releases — falls back to the Celery default
# (relative "celerybeat-schedule") when .env isn't a symlink, e.g. local dev.
_env_symlink = Path(__file__).resolve().parent.parent / ".env"
if _env_symlink.is_symlink():
    celery_app.conf.beat_schedule_filename = str(_env_symlink.resolve().parent / "celerybeat-schedule")

celery_app.conf.beat_schedule = _build_beat_schedule()
celery_app.conf.task_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.result_serializer = "json"

# Task duration logging (workers/app/core/task_timing.py)
import app.core.task_timing  # noqa: E402, F401


def _init_bugsnag() -> None:
    """Bugsnag for Celery: task-failure signal + ERROR-log handler."""
    try:
        # Opt-in: reporting only happens where the deploy env provides the key
        # (prod shared env). No key baked in — dev shells and test runs stay silent.
        key = os.getenv("BUGSNAG_API_KEY", "").strip()
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
