"""Backward-compatible shim — use crawler_registry for new code."""

from __future__ import annotations

from app.modules.scraper.crawler_registry import (
    infer_crawler_type,
    is_crawler_enabled,
    is_web_spa_enabled,
)
from app.modules.scraper.crawler_types import CrawlerType

CrawlLane = str  # deprecated alias


def crawl_lane_for_url(scrape_url: str) -> str:
    from app.modules.scraper.core.scrape_engine import uses_browser_engine

    ctype = infer_crawler_type(scrape_url)
    if ctype != CrawlerType.WEB:
        return "http"
    if uses_browser_engine(scrape_url):
        return "browser"
    return "http"


def is_lane_enabled(lane: str) -> bool:
    if lane == "browser":
        return is_web_spa_enabled() and is_crawler_enabled(CrawlerType.WEB)
    if lane == "http":
        return is_crawler_enabled(CrawlerType.WEB)
    if lane == "mail":
        return is_crawler_enabled(CrawlerType.MAIL)
    return False
