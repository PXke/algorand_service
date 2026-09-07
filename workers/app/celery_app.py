"""Celery app wiring: broker/backend config, beat schedule, Bugsnag, and prefork-safety hooks."""

from pathlib import Path

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from app.core import config
from app.core.config import env_bool, env_int, env_str
from app.modules.scraper.crawler_registry import is_crawler_enabled
from app.modules.scraper.crawler_types import CrawlerType

_broker = config.REDIS_BROKER_URL
_result_backend = config.REDIS_RESULT_URL

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
celery_app.conf.task_soft_time_limit = config.CELERY_TASK_SOFT_TIME_LIMIT
celery_app.conf.task_time_limit = config.CELERY_TASK_TIME_LIMIT


@worker_process_init.connect
def _reset_cassandra_session(**_kwargs: object) -> None:
    """cassandra-driver sessions don't survive prefork: the driver's IO event loop thread stays in the parent, so a forked child inheriting the cached session blocks forever on its first query. Drop the cache so every child opens its own connection."""
    from app.core.cassandra import get_cassandra_session

    get_cassandra_session.cache_clear()


@worker_process_init.connect
def _reset_http_client_cache(**_kwargs: object) -> None:
    """Belt-and-braces prefork safety for app.core.http_client's process-cached httpx.Client, mirroring _reset_cassandra_session above. Sync httpx.Client doesn't run a background IO thread the way the Cassandra driver does, so it doesn't share that exact failure mode -- but a client built (and used) in the parent before this fork would still hand a forked child live socket file descriptors it could corrupt by writing to alongside the parent. Dropping the cache here means every child always builds its own client/connection pool on first use. See app.core.http_client's module docstring for the full analysis."""
    from app.core.http_client import get_http_client

    get_http_client.cache_clear()


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
    "app.tasks.x402_probe",
    "app.tasks.x402_storage_reaper",
    "app.tasks.ecosystem_probe",
)


def _add_x402_beats(schedule: dict) -> None:
    """Register the x402 probe and storage-reaper beats when their gates are on.

    Probe is off by default (real requests to third-party endpoints). The
    storage reaper is off when X402_STORAGE_REAPER_TOKEN is empty -- the
    same empty=disabled gate the API route uses. Both entries set
    `expires=` to their interval so a stale tick is dropped, not run late
    (same pairing as drain-url-queue).
    """
    if config.X402_PROBE_ENABLED:
        probe_seconds = float(config.X402_PROBE_INTERVAL_SECONDS)
        schedule["x402-probe-listed-endpoints"] = {
            "task": "app.tasks.x402_probe.probe_listed_endpoints",
            "schedule": probe_seconds,
            "options": {"expires": probe_seconds},
        }
    if config.X402_STORAGE_REAPER_TOKEN.strip():
        reaper_seconds = float(config.X402_STORAGE_REAPER_INTERVAL_SECONDS)
        schedule["x402-storage-reap-expired"] = {
            "task": "app.tasks.x402_storage_reaper.reap_expired_backups",
            "schedule": reaper_seconds,
            "options": {"expires": reaper_seconds},
        }


def _add_ecosystem_probe_beat(schedule: dict) -> None:
    """Register the Algorand Open Registry liveness re-check beat when its gate is on (roadmap item 26, off by default -- same "real requests, enabled deliberately per deployment" convention as _add_x402_beats)."""
    if config.ECOSYSTEM_PROBE_ENABLED:
        probe_seconds = float(config.ECOSYSTEM_PROBE_INTERVAL_SECONDS)
        schedule["ecosystem-probe-registry-entries"] = {
            "task": "app.tasks.ecosystem_probe.probe_registry_entries",
            "schedule": probe_seconds,
            "options": {"expires": probe_seconds},
        }


def _build_beat_schedule() -> dict:
    schedule = {}
    if is_crawler_enabled(CrawlerType.CHAIN):
        schedule["chain-tail-process-rounds"] = {
            "task": "app.tasks.chain_tail.process_new_rounds",
            "schedule": float(env_int("CHAIN_TAIL_POLL_SECONDS", config.CHAIN_TAIL_POLL_SECONDS)),
        }

    if is_crawler_enabled(CrawlerType.YOUTUBE):
        schedule["youtube-poll-sources"] = {
            "task": "app.tasks.scrape.poll_youtube_sources",
            "schedule": float(env_int("YOUTUBE_POLL_SECONDS", config.YOUTUBE_POLL_SECONDS)),
        }

    if is_crawler_enabled(CrawlerType.BLUESKY):
        schedule["bluesky-poll-sources"] = {
            "task": "app.tasks.scrape.poll_bluesky_sources",
            "schedule": float(env_int("BLUESKY_POLL_SECONDS", config.BLUESKY_POLL_SECONDS)),
        }
    # Beats are a slow SAFETY-NET heartbeat: the real work is triggered on demand
    # by admin actions (approving/rejecting a review fires drain_to_compose;
    # approving a domain fires drain_url_queue + fetch_source). So these can be
    # spaced way out — workers stay idle until you accept something.
    # Same value drives both the tick interval and the task-message expiry
    # below -- a tick that's still sitting in the queue (worker pool busy)
    # past its own interval is stale and should be dropped, not run late
    # doubled-up with the tick right behind it.
    _url_queue_drain_seconds = float(
        env_int("URL_QUEUE_DRAIN_SECONDS", config.URL_QUEUE_DRAIN_SECONDS)
    )
    schedule["drain-url-queue"] = {
        "task": "app.tasks.crawler.drain_url_queue",
        # Default 10 pages / 10s: clears a new domain's 20-page initial harvest
        # in one tick, and a large backlog (e.g. an admin-approval bulk
        # backfill) in minutes instead of hours (bumped from 1/tick 2026-07-21).
        "schedule": _url_queue_drain_seconds,
        "kwargs": {"max_items": env_int("URL_QUEUE_DRAIN_BATCH", config.URL_QUEUE_DRAIN_BATCH)},
        # drain_url_queue is now single_flight-locked (CLAUDE.md invariant 5)
        # so an overlapping tick just no-ops instead of double-draining --
        # this `expires` is the companion half: a tick that never got picked
        # up by a free worker within one drain interval is discarded by
        # Celery itself rather than executing stale/backed-up later.
        "options": {"expires": _url_queue_drain_seconds},
    }
    # Companion maintenance sweep: resets any url_queue row a worker died on
    # mid-fetch (hard time_limit SIGKILL, a deploy's cold-shutdown SIGQUIT,
    # an orphaned process) back to pending, since dequeue_url() hands a row
    # to exactly one worker and nothing else ever un-sticks a stuck one. The
    # 30-minute staleness threshold itself lives in url_queue.
    # STALE_PROCESSING_SECONDS -- this interval is just how often the sweep
    # checks, same shape as reap-stale-compose-sessions/reap-stale-
    # translation-sessions above.
    schedule["reclaim-stale-processing-urls"] = {
        "task": "app.tasks.crawler.reclaim_stale_processing_urls",
        "schedule": float(
            env_int(
                "URL_QUEUE_PROCESSING_RECLAIM_SECONDS", config.URL_QUEUE_PROCESSING_RECLAIM_SECONDS
            )
        ),
    }
    # Same shape as reclaim-stale-processing-urls just above, for the other
    # in-flight marker this module's own deep-classify escalation path can
    # leave stuck: deep_classify_domain's try/finally (_clear_deep_classify_
    # queued) already clears deep_classify_queued="true" on every exit path
    # it reaches, but a hard SIGKILL past the task's own task_time_limit
    # skips it -- see reap_stale_deep_classify_flags's own docstring.
    schedule["reap-stale-deep-classify-flags"] = {
        "task": "app.tasks.crawler.reap_stale_deep_classify_flags",
        "schedule": float(env_int("DEEP_CLASSIFY_REAP_SECONDS", config.DEEP_CLASSIFY_REAP_SECONDS)),
    }
    schedule["retrain-publish-classifier"] = {
        "task": "app.tasks.crawler.retrain_publish_classifier",
        "schedule": crontab(
            minute=env_int("CLASSIFIER_RETRAIN_CRON_MINUTE", config.CLASSIFIER_RETRAIN_CRON_MINUTE),
            hour=env_int("CLASSIFIER_RETRAIN_CRON_HOUR", config.CLASSIFIER_RETRAIN_CRON_HOUR),
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
        "schedule": float(env_int("MISTRAL_DIFF_POLL_SECONDS", config.MISTRAL_DIFF_POLL_SECONDS)),
    }
    # Weekly digest retired 2026-08-18 (owner call) -- opt back in with
    # WEEKLY_DIGEST_ENABLED=1 if it's ever wanted again.
    if env_bool("WEEKLY_DIGEST_ENABLED", config.WEEKLY_DIGEST_ENABLED):
        schedule["weekly-price-analysis"] = {
            "task": "app.tasks.newspaper.publish_weekly_price_analysis",
            "schedule": crontab(
                minute=env_int("PRICE_ANALYSIS_CRON_MINUTE", config.PRICE_ANALYSIS_CRON_MINUTE),
                # Default moved off DeepSeek peak hours (2026-08-15): 9 sat inside
                # the 06:00-10:00 UTC peak window. The compose itself is also
                # gated by article_composer's off-peak check regardless of this
                # cron hour (see peak_hours.py) -- this default just avoids
                # scheduling the one hour-configurable LLM task to immediately
                # collide with peak on every run.
                hour=env_int("PRICE_ANALYSIS_CRON_HOUR", config.PRICE_ANALYSIS_CRON_HOUR),
                day_of_week=env_str("PRICE_ANALYSIS_CRON_DOW", config.PRICE_ANALYSIS_CRON_DOW),
            ),
        }
    # search_x reverted 2026-08-28 back to live per-compose calls (see
    # config.X_SEARCH_ENABLED's comment) -- the weekly sweep beat entry that
    # briefly replaced it (2026-08-25..08-28) is intentionally NOT
    # registered any more, so it stops spending on top of the now-live
    # per-compose calls. sweep_x_search_weekly / x_search_sweep.py are left
    # in place, just unreachable via beat; a manual/admin trigger of the
    # task still works (it re-checks X_SEARCH_ENABLED itself) if this ever
    # needs to be re-enabled.
    # x402 probe / monitoring (roadmap item 7) and the storage reaper
    # (roadmap item 12): pulled into _add_x402_beats so this function stays
    # under ruff's C901 branch budget -- same extraction as falcon_main.py's
    # _register_x402_storage_if_enabled.
    _add_x402_beats(schedule)
    _add_ecosystem_probe_beat(schedule)
    if is_crawler_enabled(CrawlerType.METRICS):
        schedule["collect-price-metrics"] = {
            "task": "app.tasks.metrics.collect_price_metrics",
            "schedule": float(
                env_int("PRICE_METRICS_POLL_SECONDS", config.PRICE_METRICS_POLL_SECONDS)
            ),
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
        "schedule": float(env_int("ECOSYSTEM_SYNC_SECONDS", config.ECOSYSTEM_SYNC_SECONDS)),
    }
    schedule["discover-from-mentions"] = {
        "task": "app.tasks.crawler.discover_from_mentions",
        "schedule": float(env_int("MENTION_DISCOVERY_SECONDS", config.MENTION_DISCOVERY_SECONDS)),
    }
    schedule["poll-forum-topics"] = {
        "task": "app.tasks.scrape.poll_forum_topics",
        "schedule": float(env_int("FORUM_POLL_SECONDS", config.FORUM_POLL_SECONDS)),
    }
    schedule["poll-xgov-proposals"] = {
        "task": "app.tasks.chain_tail.poll_xgov_proposals",
        "schedule": float(env_int("XGOV_POLL_SECONDS", config.XGOV_POLL_SECONDS)),
    }
    schedule["reevaluate-pending-domains"] = {
        "task": "app.tasks.crawler.reevaluate_pending_domains",
        "schedule": float(env_int("PENDING_REEVALUATE_SECONDS", config.PENDING_REEVALUATE_SECONDS)),
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
    if env_bool(
        "FRONTIER_GRAY_ZONE_RECLASSIFY_ENABLED", config.FRONTIER_GRAY_ZONE_RECLASSIFY_ENABLED
    ):
        schedule["reclassify-gray-zone-domains"] = {
            "task": "app.tasks.crawler.reclassify_gray_zone_domains",
            "schedule": float(
                env_int(
                    "FRONTIER_GRAY_ZONE_RECLASSIFY_SECONDS",
                    config.FRONTIER_GRAY_ZONE_RECLASSIFY_SECONDS,
                )
            ),
            "kwargs": {
                "limit": env_int(
                    "FRONTIER_GRAY_ZONE_RECLASSIFY_LIMIT",
                    config.FRONTIER_GRAY_ZONE_RECLASSIFY_LIMIT,
                )
            },
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
        "schedule": float(
            env_int("PUBLISH_QUEUE_DRAIN_SECONDS", config.PUBLISH_QUEUE_DRAIN_SECONDS)
        ),
    }
    # Once-daily: picks the day's to_compose slate (human pin + N-1 platform
    # picks). Runs early UTC so a "pin for tomorrow" set any time the day
    # before is captured before drain-to-compose's first run of the day.
    # drain_to_compose self-heals via _ensure_today_selected if this beat is
    # ever late/missed, so the exact hour isn't precision-critical.
    schedule["select-to-compose-for-today"] = {
        "task": "app.tasks.newspaper.select_to_compose_for_today",
        "schedule": crontab(
            minute=env_int("TO_COMPOSE_SELECT_CRON_MINUTE", config.TO_COMPOSE_SELECT_CRON_MINUTE),
            hour=env_int("TO_COMPOSE_SELECT_CRON_HOUR", config.TO_COMPOSE_SELECT_CRON_HOUR),
        ),
    }
    schedule["reap-stale-compose-sessions"] = {
        "task": "app.tasks.newspaper.reap_stale_compose_sessions",
        "schedule": float(
            env_int("COMPOSE_SESSION_REAP_SECONDS", config.COMPOSE_SESSION_REAP_SECONDS)
        ),
    }
    schedule["reap-stale-translation-sessions"] = {
        "task": "app.tasks.newspaper.reap_stale_translation_sessions",
        "schedule": float(
            env_int("TRANSLATION_SESSION_REAP_SECONDS", config.TRANSLATION_SESSION_REAP_SECONDS)
        ),
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
        "schedule": float(env_int("BROWSER_REAP_SECONDS", config.BROWSER_REAP_SECONDS)),
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
        "schedule": float(
            env_int("STALE_SELECTION_REAP_SECONDS", config.STALE_SELECTION_REAP_SECONDS)
        ),
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
        "schedule": float(env_int("DEAD_SOURCE_SWEEP_SECONDS", config.DEAD_SOURCE_SWEEP_SECONDS)),
    }
    # Editorial-room artifacts: recomputes priority for every PENDING
    # artifact once a day, feeding drain-to-compose's daily selection above.
    # Runs unconditionally (no AUTO_COMPOSE_PAUSED-style gate) -- scoring is
    # cheap pure computation, not a compose spend, so it should stay fresh
    # even while composing itself is paused.
    schedule["sweep-artifact-priorities"] = {
        "task": "app.tasks.newspaper.sweep_artifact_priorities",
        "schedule": float(
            env_int("ARTIFACT_PRIORITY_SWEEP_SECONDS", config.ARTIFACT_PRIORITY_SWEEP_SECONDS)
        ),
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
        "schedule": float(
            env_int("SERVICE_RECONCILE_SWEEP_SECONDS", config.SERVICE_RECONCILE_SWEEP_SECONDS)
        ),
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
        "schedule": float(env_int("VIEW_COUNT_FLUSH_SECONDS", config.VIEW_COUNT_FLUSH_SECONDS)),
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
        "schedule": float(env_int("ANALYTICS_FLUSH_SECONDS", config.ANALYTICS_FLUSH_SECONDS)),
    }
    # Self-heals index_article.delay() misses: that task fires once at publish
    # time with no retry, so a transient Typesense hiccup silently drops an
    # article from search forever (found 2026-08-02: a live, feed-listed
    # article missing from every result). Idempotent upsert, safe to re-run.
    schedule["reindex-articles"] = {
        "task": "app.tasks.search.reindex_articles",
        "schedule": float(env_int("ARTICLE_REINDEX_SECONDS", config.ARTICLE_REINDEX_SECONDS)),
        "kwargs": {"limit": env_int("ARTICLE_REINDEX_LIMIT", config.ARTICLE_REINDEX_LIMIT)},
    }
    if is_crawler_enabled(CrawlerType.MAIL):
        schedule["mail-poll-inbox"] = {
            "task": "app.tasks.newspaper.poll_mail_inbox",
            "schedule": float(env_int("MAIL_POLL_SECONDS", config.MAIL_POLL_SECONDS)),
        }
    # Editorial-brief recurrence (auto-assign never-run briefs + cadence
    # refresh) is OFF by default: it silently regenerated standing briefs with
    # no operator action (a 30-day brief re-ran and republished on its own,
    # 2026-07-19). Briefs now only compose when explicitly triggered via the
    # admin API. Set EDITORIAL_BRIEF_SCAN_ENABLED=true in workers.env to restore
    # the recurring beat.
    if env_bool("EDITORIAL_BRIEF_SCAN_ENABLED", config.EDITORIAL_BRIEF_SCAN_ENABLED):
        schedule["scan-editorial-brief-schedule"] = {
            "task": "app.tasks.newspaper.scan_editorial_brief_schedule",
            "schedule": float(
                env_int("EDITORIAL_BRIEF_SCAN_SECONDS", config.EDITORIAL_BRIEF_SCAN_SECONDS)
            ),
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
    celery_app.conf.beat_schedule_filename = str(
        _env_symlink.resolve().parent / "celerybeat-schedule"
    )

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
        key = env_str("BUGSNAG_API_KEY", config.BUGSNAG_API_KEY).strip()
        if not key:
            return
        import logging

        import bugsnag
        from bugsnag.celery import connect_failure_handler
        from bugsnag.handlers import BugsnagHandler

        bugsnag.configure(
            api_key=key,
            release_stage=env_str("BUGSNAG_RELEASE_STAGE", config.BUGSNAG_RELEASE_STAGE),
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
