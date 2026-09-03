"""Fetch a YouTube video's transcript (local pipeline, then third-party fallback)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.core.redis_client import get_redis

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

_USER_AGENT = "algorand-platform-newspaper/1.0 (+https://algorand.pxke.me)"


class TranscriptFetchError(Exception):
    """Raised when the metered third-party transcript API call itself failed (network error, timeout, non-2xx response).

    Distinct from a successful call that legitimately found no captions --
    that case returns "". Callers must not treat this the same as a
    confirmed no-transcript result (CLAUDE.md invariant 8): a transient
    failure should leave the video eligible for a retried fetch, not
    permanently forfeit an already-paid-for transcript.
    """


def _client() -> redis.Redis:
    return get_redis()


def _attempt_key(video_id: str) -> str:
    return f"youtube:transcript:{video_id}"


def transcript_attempted(video_id: str) -> bool:
    """Whether we've already spent a (metered) transcript call on this video.

    Survives the enqueue-gate skip paths that never write a snapshot, so a
    rejected video is not re-fetched every poll. Redis errors fail open.
    """
    if not video_id:
        return False
    try:
        return bool(_client().exists(_attempt_key(video_id)))
    except Exception:
        return False


def mark_transcript_attempted(video_id: str) -> None:
    """Record that a transcript fetch was attempted for this video, fail open."""
    if not video_id:
        return
    try:
        from app.core.config import YOUTUBE_TRANSCRIPT_ATTEMPT_TTL

        _client().set(_attempt_key(video_id), "1", ex=YOUTUBE_TRANSCRIPT_ATTEMPT_TTL)
    except Exception:
        logger.warning("failed to mark transcript attempted for %s", video_id, exc_info=True)


def _extract_from_dict(data: dict[str, Any]) -> str:
    for key in ("transcript", "text", "content", "data", "result", "segments"):
        if key in data:
            inner = _extract_transcript_text(data[key])
            if inner:
                return inner
    return ""


def _extract_from_list(data: list[Any]) -> str:
    parts: list[str] = []
    for seg in data:
        if isinstance(seg, str):
            parts.append(seg)
        elif isinstance(seg, dict):
            for key in ("text", "snippet", "content", "transcript"):
                val = seg.get(key)
                if isinstance(val, str) and val.strip():
                    parts.append(val.strip())
                    break
    return " ".join(p for p in parts if p).strip()


def _extract_transcript_text(data: Any) -> str:  # noqa: ANN401 -- arbitrary third-party transcript response shape
    """Pull plain transcript text out of common third-party response shapes.

    Handles: a bare string; {"transcript"|"text"|"content": <str or list>};
    a list of segments each with a "text"/"snippet"/"content" field.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, dict):
        return _extract_from_dict(data)
    if isinstance(data, list):
        return _extract_from_list(data)
    return ""


def _fetch_via_third_party_api(video_id: str) -> str:
    """Fetch a video transcript via the configured third-party API.

    Returns plain text, or "" when disabled/unconfigured, or when the API
    call succeeded but genuinely found no transcript for this video. Raises
    ``TranscriptFetchError`` when the request itself failed (network error,
    timeout, non-2xx status) -- a transient failure must not be silently
    coerced to the same "" a confirmed no-transcript result returns, or a
    video's transcript is forfeited forever the moment the caller marks it
    attempted (CLAUDE.md invariant 8).
    """
    from app.core.config import (
        YOUTUBE_TRANSCRIPT_API_KEY,
        YOUTUBE_TRANSCRIPT_API_URL,
        YOUTUBE_TRANSCRIPT_AUTH_HEADER,
        YOUTUBE_TRANSCRIPT_ENABLED,
        YOUTUBE_TRANSCRIPT_TIMEOUT,
    )

    if not (
        YOUTUBE_TRANSCRIPT_ENABLED and YOUTUBE_TRANSCRIPT_API_URL and YOUTUBE_TRANSCRIPT_API_KEY
    ):
        return ""
    if not video_id:
        return ""

    url = YOUTUBE_TRANSCRIPT_API_URL.format(
        video_id=video_id,
        video_url=f"https://www.youtube.com/watch?v={video_id}",
    )
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    header_name = (YOUTUBE_TRANSCRIPT_AUTH_HEADER or "x-api-key").strip()
    key = YOUTUBE_TRANSCRIPT_API_KEY
    if header_name.lower() == "authorization" and not key.lower().startswith("bearer "):
        key = f"Bearer {key}"
    headers[header_name] = key

    try:
        import httpx

        resp = httpx.get(url, headers=headers, timeout=float(YOUTUBE_TRANSCRIPT_TIMEOUT))
        resp.raise_for_status()
    except Exception as exc:
        # The request itself failed (network blip, timeout, rate limit, 5xx,
        # etc) -- distinguish this from a successful call that found nothing,
        # so the caller can retry instead of forfeiting the transcript.
        logger.warning("transcript API request failed for %s", video_id, exc_info=True)
        raise TranscriptFetchError(f"transcript API request failed: {exc}") from exc

    try:
        return _extract_transcript_text(resp.json())
    except ValueError:
        # Non-JSON provider (plain text / VTT) — return the body as-is.
        return resp.text.strip()


def _fetch_via_local_pipeline(video_id: str) -> str:
    """Local yt-dlp (proxied) -> ffmpeg -> Voxtral pipeline. "" on any failure or when disabled — never raises."""
    from app.core.config import YOUTUBE_LOCAL_TRANSCRIBE_ENABLED

    if not YOUTUBE_LOCAL_TRANSCRIBE_ENABLED or not video_id:
        return ""

    import shutil
    from pathlib import Path

    from app.modules.ai.voxtral_client import transcribe_audio
    from app.modules.scraper.core.youtube_audio import download_video_audio

    audio_path = None
    try:
        audio_path = download_video_audio(video_id)
        if not audio_path:
            return ""
        return transcribe_audio(audio_path)
    except Exception:
        logger.warning("local transcription failed for %s", video_id, exc_info=True)
        return ""
    finally:
        if audio_path:
            shutil.rmtree(Path(audio_path).parent, ignore_errors=True)


def fetch_video_transcript(video_id: str) -> str:
    """Best-effort transcript: local yt-dlp+Voxtral pipeline first (if enabled), falling back to the legacy third-party API (if configured).

    Returns plain text, or "" when genuinely unavailable (disabled/unconfigured,
    or a successful call found no captions). The local pipeline stays fully
    best-effort (never raises -- see `_fetch_via_local_pipeline`). Raises
    ``TranscriptFetchError`` if the third-party API fallback's request itself
    failed -- callers must not mark the video as a completed transcript
    attempt on that path (CLAUDE.md invariant 8).
    """
    if not video_id:
        return ""
    text = _fetch_via_local_pipeline(video_id)
    if text:
        return text
    return _fetch_via_third_party_api(video_id)
