from __future__ import annotations

from enum import StrEnum


class CrawlerType(StrEnum):
    """Distinct crawler lanes — each can be enabled/disabled in crawler_config + env."""

    WEB = "web"
    REDDIT = "reddit"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    YOUTUBE = "youtube"
    MAIL = "mail"
    CHAIN = "chain"
    METRICS = "metrics"
