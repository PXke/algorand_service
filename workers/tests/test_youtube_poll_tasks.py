"""A transient (metered third-party) transcript-API failure must not permanently forfeit an already-paid-for transcript.

Root cause: fetch_video_transcript used to collapse a genuine "no transcript
exists" result AND a transient API error (timeout, rate limit, 5xx) to the
same "" -- so the caller here always called mark_transcript_attempted right
after, and a transient blip meant the video would never be retried
(CLAUDE.md invariant 8: an error result must never be silently coerced to
the same value a genuine "none found" result produces). Fixed by having the
third-party API layer raise TranscriptFetchError on a real request failure,
distinct from a successful call that legitimately found nothing.
"""

from __future__ import annotations

import pytest

import app.modules.scraper.tasks.youtube_poll_tasks as yt_poll
from app.modules.chain_tail.registry_cache import ServiceEntry
from app.modules.scraper.core.youtube_scraper import ChannelVideo
from app.modules.scraper.core.youtube_transcript import TranscriptFetchError

_ENTRY = ServiceEntry(
    service_id="svc-1",
    display_name="Some Channel",
    match_kind="youtube_channel",
    match_value="UCabc1234567890123456789",
    scrape_url="youtube://UCabc1234567890123456789",
)
_VIDEO = ChannelVideo(
    video_id="vid-1",
    title="A new video",
    description="description text",
    published="2026-08-28T00:00:00Z",
    watch_url="https://www.youtube.com/watch?v=vid-1",
    thumbnail="",
)


def _stub_common(monkeypatch: pytest.MonkeyPatch, *, ingested: list[dict[str, object]]) -> None:
    monkeypatch.setattr(yt_poll, "is_crawler_enabled", lambda _crawler_type: True)
    monkeypatch.setattr(yt_poll, "clear_registry_cache", lambda: None)
    monkeypatch.setattr(yt_poll, "load_enabled_services", lambda: (_ENTRY,))
    monkeypatch.setattr(
        yt_poll, "fetch_channel_videos", lambda _channel_id: ("Some Channel", [_VIDEO])
    )
    # Never yet ingested, so the transcript-attempt gate is actually reached.
    monkeypatch.setattr(yt_poll, "get_latest_snapshot", lambda _source_id: None)
    monkeypatch.setattr(yt_poll, "transcript_attempted", lambda _video_id: False)

    def _fake_ingest(**kwargs: object) -> dict[str, str]:
        ingested.append(kwargs)
        return {"status": "enqueued"}

    monkeypatch.setattr(yt_poll, "ingest_publish_signal", _fake_ingest)


def test_transient_transcript_failure_does_not_mark_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TranscriptFetchError (transient API failure) must leave the video eligible for a retried transcript fetch on a later poll -- mark_transcript_attempted must NOT be called."""
    ingested: list[dict[str, object]] = []
    _stub_common(monkeypatch, ingested=ingested)

    def _boom(_video_id: str) -> str:
        raise TranscriptFetchError("simulated transient 500")

    monkeypatch.setattr(yt_poll, "fetch_video_transcript", _boom)

    marked: list[str] = []
    monkeypatch.setattr(yt_poll, "mark_transcript_attempted", marked.append)

    result = yt_poll.poll_youtube_sources()

    assert marked == []
    assert result["status"] == "ok"
    # The video is still ingested this poll, just without a transcript --
    # the fetch failure only affects retry eligibility, not this poll's
    # ingest itself.
    assert len(ingested) == 1
    assert ingested[0]["transcript_text"] == ""


def test_confirmed_no_transcript_marks_attempted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine 'no transcript available' result (fetch succeeds, returns "") DOES mark the video attempted -- we must not retry forever on a video that will never have one."""
    ingested: list[dict[str, object]] = []
    _stub_common(monkeypatch, ingested=ingested)

    monkeypatch.setattr(yt_poll, "fetch_video_transcript", lambda _video_id: "")

    marked: list[str] = []
    monkeypatch.setattr(yt_poll, "mark_transcript_attempted", marked.append)

    result = yt_poll.poll_youtube_sources()

    assert marked == ["vid-1"]
    assert result["status"] == "ok"
    assert len(ingested) == 1
    assert ingested[0]["transcript_text"] == ""


def test_successful_transcript_also_marks_attempted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity check alongside the two cases above: a successful transcript fetch marks attempted too (unchanged prior behavior) and is passed through to ingest."""
    ingested: list[dict[str, object]] = []
    _stub_common(monkeypatch, ingested=ingested)

    monkeypatch.setattr(yt_poll, "fetch_video_transcript", lambda _video_id: "real transcript text")

    marked: list[str] = []
    monkeypatch.setattr(yt_poll, "mark_transcript_attempted", marked.append)

    result = yt_poll.poll_youtube_sources()

    assert marked == ["vid-1"]
    assert result["status"] == "ok"
    assert ingested[0]["transcript_text"] == "real transcript text"
