from __future__ import annotations

import os

from app.modules.scraper.core.youtube_audio import download_video_audio

_VALID_ID = "dQw4w9WgXcQ"


def test_rejects_malformed_video_id() -> None:
    assert download_video_audio("") is None
    assert download_video_audio("not a valid id!!") is None
    assert download_video_audio("short") is None


def test_builds_proxy_opt_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.config.YOUTUBE_DOWNLOAD_PROXY_URL", "http://user:pass@proxy:8080"
    )
    monkeypatch.setattr("app.core.config.YOUTUBE_DOWNLOAD_TIMEOUT", 180)

    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, url, download=True):
            captured["url"] = url
            # Simulate yt-dlp having written the extracted mp3.
            out_dir = os.path.dirname(captured["opts"]["outtmpl"])
            with open(os.path.join(out_dir, f"{_VALID_ID}.mp3"), "wb") as f:
                f.write(b"fake audio")

    fake_yt_dlp = type("FakeModule", (), {"YoutubeDL": FakeYoutubeDL})
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake_yt_dlp)

    result = download_video_audio(_VALID_ID)
    assert result is not None
    assert result.endswith(f"{_VALID_ID}.mp3")
    assert captured["opts"]["proxy"] == "http://user:pass@proxy:8080"
    assert _VALID_ID in captured["url"]


def test_omits_proxy_opt_when_not_configured(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.YOUTUBE_DOWNLOAD_PROXY_URL", "")

    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, url, download=True):
            out_dir = os.path.dirname(captured["opts"]["outtmpl"])
            with open(os.path.join(out_dir, f"{_VALID_ID}.mp3"), "wb") as f:
                f.write(b"fake audio")

    fake_yt_dlp = type("FakeModule", (), {"YoutubeDL": FakeYoutubeDL})
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake_yt_dlp)

    download_video_audio(_VALID_ID)
    assert "proxy" not in captured["opts"]


def test_returns_none_on_download_failure(monkeypatch) -> None:
    class FakeYoutubeDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, url, download=True):
            raise RuntimeError("Sign in to confirm you're not a bot")

    fake_yt_dlp = type("FakeModule", (), {"YoutubeDL": FakeYoutubeDL})
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake_yt_dlp)

    assert download_video_audio(_VALID_ID) is None
