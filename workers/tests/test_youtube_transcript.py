"""Transcript fetch falls back from the local pipeline to the third-party API."""

from __future__ import annotations

from pathlib import Path
from typing import Never

import pytest

import app.modules.scraper.core.youtube_transcript as yt_transcript


def test_fetch_video_transcript_empty_video_id() -> None:
    """Returns an empty string immediately for an empty video id."""
    assert yt_transcript.fetch_video_transcript("") == ""


def test_fetch_video_transcript_prefers_local_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uses the local pipeline's transcript and never calls the third-party API when local succeeds."""
    monkeypatch.setattr(yt_transcript, "_fetch_via_local_pipeline", lambda _vid: "local text")

    def fail_third_party(_vid: str) -> Never:
        raise AssertionError("third-party API should not be called when local succeeds")

    monkeypatch.setattr(yt_transcript, "_fetch_via_third_party_api", fail_third_party)

    assert yt_transcript.fetch_video_transcript("abc123") == "local text"


def test_fetch_video_transcript_falls_back_to_third_party(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to the third-party API's transcript when the local pipeline returns empty."""
    monkeypatch.setattr(yt_transcript, "_fetch_via_local_pipeline", lambda _vid: "")
    monkeypatch.setattr(
        yt_transcript, "_fetch_via_third_party_api", lambda _vid: "third party text"
    )

    assert yt_transcript.fetch_video_transcript("abc123") == "third party text"


def test_fetch_video_transcript_returns_empty_when_both_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns an empty string when both the local pipeline and the third-party API yield nothing."""
    monkeypatch.setattr(yt_transcript, "_fetch_via_local_pipeline", lambda _vid: "")
    monkeypatch.setattr(yt_transcript, "_fetch_via_third_party_api", lambda _vid: "")

    assert yt_transcript.fetch_video_transcript("abc123") == ""


def test_local_pipeline_disabled_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns an empty string without attempting anything when local transcription is disabled."""
    monkeypatch.setattr("app.core.config.YOUTUBE_LOCAL_TRANSCRIBE_ENABLED", False)
    assert yt_transcript._fetch_via_local_pipeline("abc123") == ""


def test_local_pipeline_never_raises_on_download_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns an empty string instead of raising when the audio download step throws."""
    monkeypatch.setattr("app.core.config.YOUTUBE_LOCAL_TRANSCRIBE_ENABLED", True)

    def boom(_video_id: str) -> Never:
        raise RuntimeError("network exploded")

    monkeypatch.setattr("app.modules.scraper.core.youtube_audio.download_video_audio", boom)

    assert yt_transcript._fetch_via_local_pipeline("abc123") == ""


def test_local_pipeline_returns_empty_when_no_audio_downloaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skips transcription entirely and returns empty when the audio download yields no file."""
    monkeypatch.setattr("app.core.config.YOUTUBE_LOCAL_TRANSCRIBE_ENABLED", True)
    monkeypatch.setattr(
        "app.modules.scraper.core.youtube_audio.download_video_audio", lambda _vid: None
    )

    def fail_transcribe(_path: str) -> Never:
        raise AssertionError("should not attempt transcription with no audio file")

    monkeypatch.setattr("app.modules.ai.voxtral_client.transcribe_audio", fail_transcribe)

    assert yt_transcript._fetch_via_local_pipeline("abc123") == ""


def test_local_pipeline_transcribes_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Transcribes the downloaded audio file and removes its temp directory afterward."""
    monkeypatch.setattr("app.core.config.YOUTUBE_LOCAL_TRANSCRIBE_ENABLED", True)

    audio_dir = tmp_path / "yt-audio-xyz"
    audio_dir.mkdir()
    audio_path = audio_dir / "abc123.mp3"
    audio_path.write_bytes(b"fake")

    monkeypatch.setattr(
        "app.modules.scraper.core.youtube_audio.download_video_audio",
        lambda _vid: str(audio_path),
    )
    monkeypatch.setattr(
        "app.modules.ai.voxtral_client.transcribe_audio",
        lambda _path: "transcribed text",
    )

    result = yt_transcript._fetch_via_local_pipeline("abc123")
    assert result == "transcribed text"
    assert not audio_dir.exists()
