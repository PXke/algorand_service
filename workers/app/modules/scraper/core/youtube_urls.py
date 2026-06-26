from __future__ import annotations

import re
from dataclasses import dataclass

# YouTube channel ids are "UC" + 22 url-safe base64 chars.
_CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{20,}$")


@dataclass(frozen=True)
class YoutubeTarget:
    channel_id: str


def parse_youtube_target(scrape_url: str) -> YoutubeTarget | None:
    """Registry url → channel id. Accepts youtube://UC… or youtube://channel/UC…"""
    raw = (scrape_url or "").strip()
    if raw.startswith("youtube://"):
        path = raw[len("youtube://") :].strip("/")
    elif raw.startswith("youtube:"):
        path = raw[len("youtube:") :].strip("/")
    else:
        return None

    if path.startswith("channel/"):
        path = path[len("channel/") :]
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None

    channel_id = parts[0]
    if not _CHANNEL_ID.match(channel_id):
        return None
    return YoutubeTarget(channel_id=channel_id)


def is_youtube_scrape_url(scrape_url: str) -> bool:
    return parse_youtube_target(scrape_url) is not None


def youtube_feed_url(channel_id: str) -> str:
    """Public Atom feed of a channel's recent uploads (latest ~15, metadata only)."""
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def youtube_channel_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}"
