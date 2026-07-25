"""Fetch a YouTube channel's recent videos via its RSS feed."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from app.core.net_guard import guarded_get
from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.youtube_urls import (
    parse_youtube_target,
    youtube_channel_url,
    youtube_feed_url,
)

_USER_AGENT = "algorand-platform-newspaper/1.0 (+https://algorand.pxke.me)"

# Atom + YouTube/MediaRSS namespaces used in the channel feed.
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

# Cap the recent videos pulled per poll (the feed carries ~15).
_MAX_VIDEOS = 15


@dataclass(frozen=True)
class ChannelVideo:
    """One video from a YouTube channel's RSS feed."""
    video_id: str
    title: str
    description: str
    published: str
    watch_url: str
    thumbnail: str


def fetch_channel_videos(channel_id: str) -> tuple[str, list[ChannelVideo]]:
    """Fetch a channel's recent uploads from the public Atom feed.

    Returns (channel_title, videos). Metadata only — no transcript (the caption
    endpoint needs a po_token; see Stage 2).
    """
    response = guarded_get(
        youtube_feed_url(channel_id),
        timeout=20.0,
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()

    root = ET.fromstring(response.text)
    channel_title = (root.findtext("atom:title", default="", namespaces=_NS) or "").strip()

    videos: list[ChannelVideo] = []
    for entry in root.findall("atom:entry", _NS)[:_MAX_VIDEOS]:
        video_id = (entry.findtext("yt:videoId", default="", namespaces=_NS) or "").strip()
        if not video_id:
            continue
        title = (entry.findtext("atom:title", default="", namespaces=_NS) or "").strip()
        published = (entry.findtext("atom:published", default="", namespaces=_NS) or "").strip()
        description = ""
        thumbnail = ""
        group = entry.find("media:group", _NS)
        if group is not None:
            description = (
                group.findtext("media:description", default="", namespaces=_NS) or ""
            ).strip()
            thumb = group.find("media:thumbnail", _NS)
            if thumb is not None and (thumb.get("url") or "").strip():
                thumbnail = thumb.get("url").strip()
        videos.append(
            ChannelVideo(
                video_id=video_id,
                title=title,
                description=description,
                published=published,
                watch_url=f"https://www.youtube.com/watch?v={video_id}",
                thumbnail=thumbnail,
            )
        )
    return channel_title, videos


class YoutubeScraper(BaseScraper):
    """Channel uploads via the public Atom feed, as one digest snapshot.

    Retained for the generic scrape path; the youtube poll lane now ingests one
    signal per video (see youtube_poll_tasks) rather than this aggregate digest.
    """

    def scrape(self, url: str, source_id: str) -> ScrapeResult:
        """Scrape a channel's recent uploads via its public Atom feed, as one digest snapshot."""
        target = parse_youtube_target(url)
        if target is None:
            msg = f"not a youtube scrape url: {url!r}"
            raise ValueError(msg)

        channel_title, videos = fetch_channel_videos(target.channel_id)
        og_image = next((v.thumbnail for v in videos if v.thumbnail), "")
        blocks = [
            "\n".join(p for p in (v.title, v.published, v.watch_url, v.description) if p)
            for v in videos
        ]
        text = "\n\n".join(b for b in blocks if b)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ScrapeResult(
            source_id=source_id,
            url=youtube_channel_url(target.channel_id),
            title=channel_title or "YouTube channel",
            text=text,
            content_hash=content_hash,
            raw_html="",
            og_image=og_image,
        )
