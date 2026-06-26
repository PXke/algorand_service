from __future__ import annotations

from urllib.parse import urlparse

from app.core import config
from app.modules.newspaper.official_channels_store import load_official_channel_ids
from app.modules.scraper.core.discord_urls import parse_discord_channel_id


def _parse_csv_set(raw: str) -> set[str]:
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _official_set(kind: str, env_raw: str) -> set[str]:
    """Union of env allowlist and admin-managed official_channels table."""
    return _parse_csv_set(env_raw) | load_official_channel_ids(kind)


def source_trust_bonus(
    *,
    source_kind: str | None,
    source_url: str = "",
    mail_from: str = "",
) -> int:
    """
    Authority boost for queue priority (0–25).
    Official Foundation mail/Discord rank highest.
    """
    kind = (source_kind or "").strip().lower()
    bonus = 0

    if kind in ("push", "local_browser", "firefox_extension"):
        bonus = 18
    elif kind == "mail":
        bonus = 12
        domain = _mail_domain(mail_from)
        if domain and domain in _official_set("mail_domain", config.OFFICIAL_MAIL_FROM_DOMAINS):
            bonus = 25
    elif kind == "discord":
        bonus = 8
        channel_id = parse_discord_channel_id(source_url) or ""
        official = _official_set("discord", config.OFFICIAL_DISCORD_CHANNEL_IDS)
        if channel_id and channel_id in official:
            bonus = 20
    elif kind == "telegram":
        bonus = 8
        if _telegram_chat_trusted(source_url):
            bonus = 18
    elif kind == "reddit":
        bonus = 5
    elif kind == "web":
        host = urlparse(source_url).netloc.lower()
        if host.endswith("algorand.foundation") or host.endswith("algorand.com"):
            bonus = 15

    return min(25, max(0, bonus))


def _mail_domain(mail_from: str) -> str:
    raw = mail_from.strip().lower()
    if "<" in raw and ">" in raw:
        raw = raw.split("<", 1)[1].split(">", 1)[0]
    if "@" in raw:
        return raw.rsplit("@", 1)[-1]
    return raw


def _telegram_chat_trusted(source_url: str) -> bool:
    trusted = _official_set("telegram", config.OFFICIAL_TELEGRAM_CHAT_IDS)
    if not trusted:
        return False
    lower = source_url.lower()
    return any(token in lower for token in trusted)
