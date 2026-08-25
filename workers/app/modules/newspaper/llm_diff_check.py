"""Poll all scrape sources for diffs and route significant ones into the publish queue."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from app.core.config import mistral_configured
from app.modules.chain_tail.registry_cache import (
    ServiceEntry,
    clear_registry_cache,
    load_enabled_services,
)
from app.modules.newspaper.tasks.publish_tasks import run_publish_pipeline
from app.modules.scraper.core.scrape_cooldown import mark_scraped, scrape_throttled

PublishFn = Callable[..., dict[str, str]]


def _has_pending_classifier_review() -> bool:
    from app.modules.crawler.classifier_review_store import count_pending_reviews

    return count_pending_reviews() > 0


def _has_pending_feed_release() -> bool:
    from algorand_shared.article_transitions import list_backlog_articles

    return bool(list_backlog_articles())


def run_llm_diff_check(
    *,
    publish: PublishFn = run_publish_pipeline,
    load_services: Callable[[], tuple[ServiceEntry, ...]] = load_enabled_services,
    clear_cache: Callable[[], None] = clear_registry_cache,
    has_pending_classifier_review: Callable[[], bool] = _has_pending_classifier_review,
    has_pending_feed_release: Callable[[], bool] = _has_pending_feed_release,
    pause_on_feed_backlog: bool | None = None,
    is_throttled: Callable[[str], bool] = scrape_throttled,
    record_scrape: Callable[..., None] = mark_scraped,
) -> dict[str, object]:
    """Poll scrape sources, detect snapshot diffs, and publish via the LLM writer when content changed."""
    from app.core import config

    if pause_on_feed_backlog is None:
        pause_on_feed_backlog = config.PAUSE_INTAKE_ON_FEED_BACKLOG
    if config.AUTO_COMPOSE_PAUSED:
        return {"status": "skipped", "reason": "auto_compose_paused", "checked": 0}
    if not mistral_configured():
        return {"status": "skipped", "reason": "mistral_not_configured", "checked": 0}
    if has_pending_classifier_review():
        return {"status": "skipped", "reason": "classifier_review_pending", "checked": 0}
    # The feed-release backlog drains on its own ~1/h schedule; only let it pause
    # new intake when explicitly opted in (off by default — see config).
    if pause_on_feed_backlog and has_pending_feed_release():
        return {"status": "skipped", "reason": "approved_feed_pending_release", "checked": 0}

    clear_cache()

    # Web sources only — reddit/discord/telegram have dedicated pollers, so
    # scraping them here too would double the request rate and trigger limits.
    def _is_web(u: str) -> bool:
        u = (u or "").lower()
        return u.startswith("http://") or u.startswith("https://")

    entries = [e for e in load_services() if e.scrape_url and _is_web(e.scrape_url)]

    # Watch cadence: this beat fires every ~10 min so NEW services get their
    # first snapshot promptly, but a healthy source is only re-scraped once per
    # SERVICE_RESCRAPE_DAYS (weekly by default) — the diff between those scrapes
    # is the update story (event-driven/breaking publishes don't go through
    # this beat).
    results: list[dict[str, str]] = []
    checked = 0
    throttled = 0
    for entry in entries:
        if is_throttled(entry.service_id):
            throttled += 1
            continue
        checked += 1
        trigger_id = f"mistral-diff-{uuid.uuid4().hex[:16]}"
        try:
            outcome = publish(
                service_id=entry.service_id,
                display_name=entry.display_name,
                scrape_url=entry.scrape_url or "",
                match_kind=entry.match_kind,
                match_value=entry.match_value,
                txid=trigger_id,
                round_num=0,
                mistral_only=True,
            )
        except Exception as exc:
            # One bad source (dead domain, scrape error) must not abort the whole
            # batch — record and continue to the next.
            outcome = {"status": "error", "reason": str(exc)[:200]}
        # Stamp the throttle after the attempt. Success stamps the full
        # re-scrape window (weekly cadence); an error stamps only the short
        # cooldown so the source retries soon instead of losing a whole window
        # — while still not being re-hammered every beat.
        record_scrape(entry.service_id, ok=outcome.get("status") != "error")
        results.append({"service_id": entry.service_id, **outcome})

    summary = _summarize_results(results)
    return {
        "status": "ok",
        "checked": checked,
        "throttled": throttled,
        "results": results,
        **summary,
    }


def _summarize_results(results: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in results:
        status = row.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "published": counts.get("published", 0),
        "unchanged": counts.get("unchanged", 0),
        "mistral_failed": counts.get("mistral_failed", 0),
    }
