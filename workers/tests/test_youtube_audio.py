"""YouTube audio download proxy configuration and failure handling."""

from __future__ import annotations

from pathlib import Path
from typing import Never, Self

import pytest

from app.modules.scraper.core.youtube_audio import download_video_audio

_VALID_ID = "dQw4w9WgXcQ"


def test_rejects_malformed_video_id() -> None:
    """Returns None for empty, invalid-character, or too-short video ids without attempting a download."""
    assert download_video_audio("") is None
    assert download_video_audio("not a valid id!!") is None
    assert download_video_audio("short") is None


def test_builds_proxy_opt_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passes the configured proxy URL through to yt-dlp's options when downloading audio."""
    monkeypatch.setattr("app.core.config.YOUTUBE_DOWNLOAD_PROXY_URL", "http://user:pass@proxy:8080")
    monkeypatch.setattr("app.core.config.YOUTUBE_DOWNLOAD_TIMEOUT", 180)

    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts: dict) -> None:
            captured["opts"] = opts

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, download: bool = True) -> None:  # noqa: ARG002 -- name must match the real callee's keyword arg
            captured["url"] = url
            # Simulate yt-dlp having written the extracted mp3.
            out_dir = Path(captured["opts"]["outtmpl"]).parent
            with (out_dir / f"{_VALID_ID}.mp3").open("wb") as f:
                f.write(b"fake audio")

    fake_yt_dlp = type("FakeModule", (), {"YoutubeDL": FakeYoutubeDL})
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake_yt_dlp)

    result = download_video_audio(_VALID_ID)
    assert result is not None
    assert result.endswith(f"{_VALID_ID}.mp3")
    assert captured["opts"]["proxy"] == "http://user:pass@proxy:8080"
    assert _VALID_ID in captured["url"]


def test_omits_proxy_opt_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omits the proxy option entirely from yt-dlp's options when no proxy URL is configured."""
    monkeypatch.setattr("app.core.config.YOUTUBE_DOWNLOAD_PROXY_URL", "")

    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts: dict) -> None:
            captured["opts"] = opts

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, _url: str, download: bool = True) -> None:  # noqa: ARG002 -- name must match the real callee's keyword arg
            out_dir = Path(captured["opts"]["outtmpl"]).parent
            with (out_dir / f"{_VALID_ID}.mp3").open("wb") as f:
                f.write(b"fake audio")

    fake_yt_dlp = type("FakeModule", (), {"YoutubeDL": FakeYoutubeDL})
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake_yt_dlp)

    download_video_audio(_VALID_ID)
    assert "proxy" not in captured["opts"]


def test_returns_none_on_download_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None instead of raising when yt-dlp's extraction fails (e.g. bot-check block)."""
    class FakeYoutubeDL:
        def __init__(self, opts: dict) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, _url: str, download: bool = True) -> Never:  # noqa: ARG002 -- name must match the real callee's keyword arg
            raise RuntimeError("Sign in to confirm you're not a bot")

    fake_yt_dlp = type("FakeModule", (), {"YoutubeDL": FakeYoutubeDL})
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake_yt_dlp)

    assert download_video_audio(_VALID_ID) is None
