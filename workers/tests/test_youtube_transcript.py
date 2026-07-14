from __future__ import annotations

import app.modules.scraper.core.youtube_transcript as yt_transcript


def test_fetch_video_transcript_empty_video_id() -> None:
    assert yt_transcript.fetch_video_transcript("") == ""


def test_fetch_video_transcript_prefers_local_pipeline(monkeypatch) -> None:
    monkeypatch.setattr(yt_transcript, "_fetch_via_local_pipeline", lambda vid: "local text")

    def fail_third_party(vid):
        raise AssertionError("third-party API should not be called when local succeeds")

    monkeypatch.setattr(yt_transcript, "_fetch_via_third_party_api", fail_third_party)

    assert yt_transcript.fetch_video_transcript("abc123") == "local text"


def test_fetch_video_transcript_falls_back_to_third_party(monkeypatch) -> None:
    monkeypatch.setattr(yt_transcript, "_fetch_via_local_pipeline", lambda vid: "")
    monkeypatch.setattr(
        yt_transcript, "_fetch_via_third_party_api", lambda vid: "third party text"
    )

    assert yt_transcript.fetch_video_transcript("abc123") == "third party text"


def test_fetch_video_transcript_returns_empty_when_both_fail(monkeypatch) -> None:
    monkeypatch.setattr(yt_transcript, "_fetch_via_local_pipeline", lambda vid: "")
    monkeypatch.setattr(yt_transcript, "_fetch_via_third_party_api", lambda vid: "")

    assert yt_transcript.fetch_video_transcript("abc123") == ""


def test_local_pipeline_disabled_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.YOUTUBE_LOCAL_TRANSCRIBE_ENABLED", False)
    assert yt_transcript._fetch_via_local_pipeline("abc123") == ""


def test_local_pipeline_never_raises_on_download_exception(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.YOUTUBE_LOCAL_TRANSCRIBE_ENABLED", True)

    def boom(video_id):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(
        "app.modules.scraper.core.youtube_audio.download_video_audio", boom
    )

    assert yt_transcript._fetch_via_local_pipeline("abc123") == ""


def test_local_pipeline_returns_empty_when_no_audio_downloaded(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.YOUTUBE_LOCAL_TRANSCRIBE_ENABLED", True)
    monkeypatch.setattr(
        "app.modules.scraper.core.youtube_audio.download_video_audio", lambda vid: None
    )

    def fail_transcribe(path):
        raise AssertionError("should not attempt transcription with no audio file")

    monkeypatch.setattr(
        "app.modules.ai.voxtral_client.transcribe_audio", fail_transcribe
    )

    assert yt_transcript._fetch_via_local_pipeline("abc123") == ""


def test_local_pipeline_transcribes_and_cleans_up(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.core.config.YOUTUBE_LOCAL_TRANSCRIBE_ENABLED", True)

    audio_dir = tmp_path / "yt-audio-xyz"
    audio_dir.mkdir()
    audio_path = audio_dir / "abc123.mp3"
    audio_path.write_bytes(b"fake")

    monkeypatch.setattr(
        "app.modules.scraper.core.youtube_audio.download_video_audio",
        lambda vid: str(audio_path),
    )
    monkeypatch.setattr(
        "app.modules.ai.voxtral_client.transcribe_audio",
        lambda path: "transcribed text",
    )

    result = yt_transcript._fetch_via_local_pipeline("abc123")
    assert result == "transcribed text"
    assert not audio_dir.exists()
