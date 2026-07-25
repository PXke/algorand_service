"""Local YouTube audio download for transcription (yt-dlp + ffmpeg).

Direct download from the prod host is bot-blocked by YouTube (confirmed live
— channel/video listing via the public Atom feed in youtube_scraper.py is
unaffected, only actual media download is blocked), so this goes through a
residential/rotating proxy (YOUTUBE_DOWNLOAD_PROXY_URL). This module bypasses
app.core.net_guard entirely — yt-dlp does its own networking, not httpx — so
video_id gets its own input validation here.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile

logger = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")


def download_video_audio(video_id: str) -> str | None:
    """Download bestaudio for video_id via yt-dlp, extract to a compact mp3 with ffmpeg, and return the local file path. Returns None on any failure (bot-block, proxy auth, missing ffmpeg, disk, etc.) — never raises.

    Caller owns cleanup of the returned file's parent directory.
    """
    if not video_id or not _VIDEO_ID_RE.match(video_id):
        return None

    from app.core.config import YOUTUBE_DOWNLOAD_PROXY_URL, YOUTUBE_DOWNLOAD_TIMEOUT

    tmpdir = tempfile.mkdtemp(prefix="yt-audio-")
    try:
        import yt_dlp

        ydl_opts: dict = {
            "format": "bestaudio/best",
            "outtmpl": f"{tmpdir}/%(id)s.%(ext)s",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "64",
                }
            ],
            "socket_timeout": YOUTUBE_DOWNLOAD_TIMEOUT,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        if YOUTUBE_DOWNLOAD_PROXY_URL:
            ydl_opts["proxy"] = YOUTUBE_DOWNLOAD_PROXY_URL

        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(watch_url, download=True)

        audio_path = f"{tmpdir}/{video_id}.mp3"
        from pathlib import Path

        if Path(audio_path).exists():
            return audio_path
        logger.warning("yt-dlp reported success but no mp3 found for %s", video_id)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None
    except Exception:
        logger.warning("local audio download failed for %s", video_id, exc_info=True)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None
