"""Scraper-engine selection for a given URL/domain."""

from __future__ import annotations

from app.modules.scraper.crawler_dispatch import get_scraper_for_url

__all__ = ["get_scraper_for_url"]
