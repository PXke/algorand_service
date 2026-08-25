"""Celery task that polls monitored YouTube channels."""

from __future__ import annotations

from app.celery_app import celery_app
from app.modules.chain_tail.registry_cache import clear_registry_cache, load_enabled_services
from app.modules.newspaper.ingest_signal import ingest_publish_signal
from app.modules.newspaper.snapshot_store import get_latest_snapshot, source_id_for_service
from app.modules.scraper.core.youtube_scraper import fetch_channel_videos
from app.modules.scraper.core.youtube_transcript import (
    fetch_video_transcript,
    mark_transcript_attempted,
    transcript_attempted,
)
from app.modules.scraper.core.youtube_urls import is_youtube_scrape_url, parse_youtube_target
from app.modules.scraper.crawler_registry import is_crawler_enabled
from app.modules.scraper.crawler_types import CrawlerType


@celery_app.task(name="app.tasks.scrape.poll_youtube_sources")
def poll_youtube_sources() -> dict[str, object]:
    """Per-video ingest of YouTube channel uploads (public Atom feed).

    One signal per video, each with its own ``service_id`` (``<channel>:<videoId>``)
    so re-polling the same video hits the snapshot dedup and returns ``unchanged``
    instead of republishing — no extra dedup store needed. Metadata only;
    ``transcript_text`` is left empty for Stage 2 (audio->Whisper). One fetch of
    the public feed per source — cheap, no API key.
    """
    if not is_crawler_enabled(CrawlerType.YOUTUBE):
        return {"status": "skipped", "reason": "crawler_youtube_disabled", "sources": 0}

    clear_registry_cache()
    entries = [
        e for e in load_enabled_services() if e.scrape_url and is_youtube_scrape_url(e.scrape_url)
    ]

    new_videos = 0
    results: list[dict[str, str]] = []
    for entry in entries:
        target = parse_youtube_target(entry.scrape_url or "")
        if target is None:
            continue
        try:
            channel_title, videos = fetch_channel_videos(target.channel_id)
        except Exception as exc:
            results.append(
                {"service_id": entry.service_id, "status": "error", "detail": str(exc)[:160]}
            )
            continue

        for video in videos:
            page_text = "\n\n".join(p for p in (video.title, video.description) if p)
            if not page_text:
                continue
            service_id = f"{entry.service_id}:{video.video_id}"
            # Already ingested? Skip before the (paid) transcript call — re-polls
            # must not re-fetch transcripts for the same 15 videos every hour.
            if get_latest_snapshot(source_id_for_service(service_id)) is not None:
                results.append({"video_id": video.video_id, "status": "unchanged"})
                continue
            # Best-effort transcript (metered third-party API). A new video on a
            # monitored channel is on-topic by definition; pay at most once per
            # video, even when a skip path leaves no snapshot to dedup next poll.
            transcript = ""
            if not transcript_attempted(video.video_id):
                transcript = fetch_video_transcript(video.video_id)
                mark_transcript_attempted(video.video_id)
            outcome = ingest_publish_signal(
                service_id=service_id,
                display_name=entry.display_name,
                source_url=video.watch_url,
                page_title=video.title or channel_title,
                page_text=page_text,
                source_kind="youtube",
                match_kind="youtube_video",
                match_value=video.video_id,
                txid=f"youtube-{video.video_id}",
                transcript_text=transcript,
                og_image=video.thumbnail,
                # Each video mints its own per-item service_id ("<channel>:
                # <videoId>"), which can never literal-match a prior
                # published article's service_id even though the channel
                # itself (entry.service_id, the channel's own service_registry
                # row) is a well-covered venue — pass it through so the
                # editorial-room artifact pool correctly reads a new video on
                # an established channel as routine coverage (UPDATE_POOL),
                # not a new-service discovery.
                venue_service_id=entry.service_id,
            )
            if outcome.get("status") == "enqueued":
                new_videos += 1
            results.append(
                {"video_id": video.video_id, "transcript_chars": str(len(transcript)), **outcome}
            )

    return {
        "status": "ok",
        "sources": len(entries),
        "new_videos": new_videos,
        "results": results[:60],
    }
