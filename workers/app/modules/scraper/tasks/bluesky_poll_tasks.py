"""Celery task that polls monitored Bluesky accounts."""

from __future__ import annotations

from app.celery_app import celery_app
from app.modules.chain_tail.registry_cache import clear_registry_cache, load_enabled_services
from app.modules.newspaper.ingest_signal import ingest_publish_signal
from app.modules.newspaper.snapshot_store import get_latest_snapshot, source_id_for_service
from app.modules.scraper.core.bluesky_scraper import (
    BlueskyPost,
    fetch_author_posts,
    is_bluesky_scrape_url,
)
from app.modules.scraper.crawler_registry import is_crawler_enabled
from app.modules.scraper.crawler_types import CrawlerType


def _enqueue_post_links(post: BlueskyPost) -> None:
    """Mention-based discovery: a monitored account linking a site is a lead — feed it to the crawl frontier (unknown domains land pending as usual). Best-effort; never blocks the post ingest."""
    from urllib.parse import urlparse

    from app.modules.crawler.ecosystem_sync import _skippable
    from app.modules.crawler.url_queue import enqueue_url

    for url in getattr(post, "links", ()) or ():
        try:
            host = (urlparse(url).hostname or "").lower().removeprefix("www.")
            if not host or "." not in host or host == "bsky.app" or _skippable(host):
                continue
            enqueue_url(url, source="bluesky-mention", priority=30)
        except Exception:
            continue


@celery_app.task(name="app.tasks.scrape.poll_bluesky_sources")
def poll_bluesky_sources() -> dict[str, object]:
    """Per-post ingest of Bluesky accounts registered as services (scrape_url is a bsky.app profile URL). One signal per original post, each with its own ``service_id`` (``<service>:<rkey>``) so a re-poll hits the snapshot dedup and returns ``unchanged`` — no separate seen-store. Public AppView, no auth."""
    if not is_crawler_enabled(CrawlerType.BLUESKY):
        return {"status": "skipped", "reason": "crawler_bluesky_disabled", "sources": 0}

    from app.core.config import BLUESKY_MAX_POSTS_PER_SOURCE

    clear_registry_cache()
    entries = [
        e for e in load_enabled_services() if e.scrape_url and is_bluesky_scrape_url(e.scrape_url)
    ]

    new_posts = 0
    results: list[dict[str, str]] = []
    for entry in entries:
        try:
            display_name, posts = fetch_author_posts(
                entry.scrape_url or "", limit=BLUESKY_MAX_POSTS_PER_SOURCE
            )
        except Exception as exc:
            results.append(
                {"service_id": entry.service_id, "status": "error", "detail": str(exc)[:160]}
            )
            continue

        for post in posts:
            # Originals only: reposts/replies are conversation, not the account's
            # own product news.
            if post.is_repost or post.is_reply or not post.text:
                continue
            service_id = f"{entry.service_id}:{post.rkey}"
            if get_latest_snapshot(source_id_for_service(service_id)) is not None:
                results.append({"rkey": post.rkey, "status": "unchanged"})
                continue
            _enqueue_post_links(post)
            outcome = ingest_publish_signal(
                service_id=service_id,
                display_name=entry.display_name or display_name,
                source_url=post.web_url,
                page_title=post.text[:120],
                page_text=post.text,
                source_kind="bluesky",
                match_kind="bluesky_post",
                match_value=post.rkey,
                txid=f"bluesky-{post.rkey}",
                published_at=post.created_at,
                # service_id is per-post (one snapshot key per rkey), so
                # "previous is None" is always true here — the account itself
                # is already a known, monitored service, never a fresh
                # discovery. Without this override every post misclassifies
                # as SERVICE_DISCOVERY/new_service.
                is_first_override=False,
            )
            if outcome.get("status") == "enqueued":
                new_posts += 1
            results.append({"rkey": post.rkey, **outcome})

    return {
        "status": "ok",
        "sources": len(entries),
        "new_posts": new_posts,
        "results": results[:60],
    }
