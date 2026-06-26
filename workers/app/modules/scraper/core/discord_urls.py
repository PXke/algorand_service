from __future__ import annotations

import re

_CHANNEL_ID = re.compile(r"^\d{17,20}$")
_GUILD_CHANNEL = re.compile(
    r"^(?:https?://)?(?:www\.)?discord\.com/channels/(?P<guild>\d+)/(?P<channel>\d+)",
    re.I,
)


def parse_discord_channel_id(scrape_url: str) -> str | None:
    """Legacy: channel id only (bot API)."""
    raw = scrape_url.strip()
    if raw.startswith("discord://"):
        path = raw[len("discord://") :].strip("/")
        if path.startswith("channel/"):
            path = path[len("channel/") :]
        if path.startswith("channels/"):
            parts = path.split("/")
            if len(parts) >= 2 and _CHANNEL_ID.match(parts[1]):
                return parts[1]
        channel_id = path.split("/")[0].strip()
        return channel_id if _CHANNEL_ID.match(channel_id) else None
    if raw.startswith("discord:"):
        channel_id = raw[len("discord:") :].strip()
        return channel_id if _CHANNEL_ID.match(channel_id) else None
    return None


def is_discord_scrape_url(scrape_url: str) -> bool:
    raw = scrape_url.strip().lower()
    if raw.startswith("discord://") or raw.startswith("discord:"):
        return True
    if "discord.com" in raw and "/channels/" in raw:
        return True
    return parse_discord_channel_id(scrape_url) is not None


def resolve_discord_web_url(scrape_url: str) -> str | None:
    """Map registry URL to a discord.com page loadable in a browser."""
    raw = scrape_url.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        if "discord.com" in raw:
            return raw
        return None

    if raw.startswith("discord://web/"):
        return raw[len("discord://web/") :].strip()

    if raw.startswith("discord://"):
        path = raw[len("discord://") :].strip("/")
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if path.startswith("channels/"):
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2 and _CHANNEL_ID.match(parts[0]) and _CHANNEL_ID.match(parts[1]):
                return f"https://discord.com/channels/{parts[0]}/{parts[1]}"
        return None

    match = _GUILD_CHANNEL.match(raw)
    if match:
        return f"https://discord.com/channels/{match.group('guild')}/{match.group('channel')}"

    return None
