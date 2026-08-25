"""Forum hot-topic lane: community debates as publish signals.

forum.algorand.co (Discourse) is where governance arguments, incident reports,
and builder questions surface first — a story class the crawler never sees
unless some page links it. One JSON GET per poll reads /latest.json; topics
that cross the engagement thresholds get one signal each (snapshot-deduped by
topic id, same pattern as the Bluesky per-post lane), with the opening post
and top replies as the writer's page_text.
"""

from __future__ import annotations

import logging
from typing import Any

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _strip_html(cooked: str) -> str:
    from bs4 import BeautifulSoup

    return BeautifulSoup(cooked or "", "html.parser").get_text(" ", strip=True)


def topic_is_hot(topic: dict[str, Any], *, min_posts: int, min_likes: int) -> bool:
    """Engagement gate. Pinned topics (site housekeeping like the scam-warning banner) never qualify no matter their stats."""
    if topic.get("pinned"):
        return False
    return (
        int(topic.get("posts_count") or 0) >= min_posts
        or int(topic.get("like_count") or 0) >= min_likes
    )


def fetch_topic_text(
    base_url: str, topic_id: int, *, max_posts: int = 5, max_chars: int = 8000
) -> tuple[str, str]:
    """(text, created_at) — the opening post plus the first replies, stripped to plain text. Authors are attributed so the writer can quote properly."""
    from app.core.net_guard import guarded_get

    resp = guarded_get(f"{base_url}/t/{topic_id}.json", timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    posts = (data.get("post_stream") or {}).get("posts") or []
    parts: list[str] = []
    for post in posts[:max_posts]:
        author = str(post.get("username") or "unknown")
        body = _strip_html(str(post.get("cooked") or ""))
        if body:
            parts.append(f"@{author}: {body}")
    created_at = str((posts[0].get("created_at") if posts else "") or "")
    return "\n\n".join(parts)[:max_chars], created_at


@celery_app.task(name="app.tasks.scrape.poll_forum_topics")
def poll_forum_topics() -> dict[str, object]:
    """Celery task: poll the Discourse forum's latest topics and enqueue publish signals."""
    from app.core import config
    from app.core.net_guard import guarded_get
    from app.modules.newspaper.ingest_signal import ingest_publish_signal
    from app.modules.newspaper.snapshot_store import get_latest_snapshot, source_id_for_service

    if not config.FORUM_POLL_ENABLED:
        return {"status": "skipped", "reason": "forum_poll_disabled"}

    base = config.FORUM_BASE_URL.rstrip("/")
    try:
        resp = guarded_get(f"{base}/latest.json", timeout=15.0)
        resp.raise_for_status()
        topics = (resp.json().get("topic_list") or {}).get("topics") or []
    except Exception as exc:
        return {"status": "error", "detail": str(exc)[:200]}

    new_signals = 0
    results: list[dict[str, object]] = []
    for topic in topics:
        topic_id = int(topic.get("id") or 0)
        title = str(topic.get("title") or "").strip()
        if not topic_id or not title:
            continue
        if not topic_is_hot(
            topic, min_posts=config.FORUM_MIN_POSTS, min_likes=config.FORUM_MIN_LIKES
        ):
            continue
        service_id = f"forum-topic:{topic_id}"
        if get_latest_snapshot(source_id_for_service(service_id)) is not None:
            continue
        try:
            text, created_at = fetch_topic_text(base, topic_id)
        except Exception as exc:
            results.append({"topic_id": topic_id, "status": "error", "detail": str(exc)[:160]})
            continue
        if not text:
            continue
        slug = str(topic.get("slug") or topic_id)
        outcome = ingest_publish_signal(
            service_id=service_id,
            display_name="Algorand Forum",
            source_url=f"{base}/t/{slug}/{topic_id}",
            page_title=title[:150],
            page_text=text,
            source_kind="forum",
            match_kind="forum_topic",
            match_value=str(topic_id),
            txid=f"forum-{topic_id}",
            published_at=created_at,
            # The forum is a known venue; a hot thread is a content event,
            # never the discovery of a new service.
            is_first_override=False,
            # Every topic mints its own per-item service_id ("forum-topic:
            # <id>"), which can never literal-match a prior published
            # article's service_id even though forum.algorand.co itself is a
            # well-covered venue — pass its real registry service_id so the
            # editorial-room artifact pool correctly reads this as routine
            # coverage (UPDATE_POOL), not a new-service discovery.
            venue_service_id=config.FORUM_VENUE_SERVICE_ID,
        )
        if outcome.get("status") == "enqueued":
            new_signals += 1
        results.append({"topic_id": topic_id, **outcome})

    return {
        "status": "ok",
        "topics_seen": len(topics),
        "new_signals": new_signals,
        "results": results[:40],
    }
