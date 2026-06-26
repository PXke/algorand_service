from __future__ import annotations

import os

from app.core import config
from app.core.config import env_bool
from app.modules.scraper.core.scrape_engine import uses_browser_engine
from app.modules.scraper.core.youtube_urls import is_youtube_scrape_url
from app.modules.scraper.crawler_store import load_crawler_config
from app.modules.scraper.crawler_types import CrawlerType


def _env_override(crawler_type: str) -> bool | None:
    key = f"CRAWLER_{crawler_type.upper()}_ENABLED"
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return None
    return env_bool(key, False)


def is_crawler_enabled(crawler_type: str | CrawlerType) -> bool:
    """
    Whether a crawler lane may run. Env `CRAWLER_<TYPE>_ENABLED` overrides DB.
    Legacy: CRAWLER_HTTP_ENABLED → web; CRAWLER_BROWSER_ENABLED → web SPA sub-mode.
    """
    ctype = str(crawler_type).strip().lower()

    override = _env_override(ctype)
    if override is not None:
        return override

    if ctype == "web" and os.getenv("CRAWLER_HTTP_ENABLED") is not None:
        return config.CRAWLER_HTTP_ENABLED
    if ctype == "mail" and os.getenv("CRAWLER_MAIL_ENABLED") is not None:
        return config.CRAWLER_MAIL_ENABLED

    row = load_crawler_config().get(ctype)
    if row is None:
        return False
    return row.enabled


def is_web_spa_enabled() -> bool:
    """Playwright inside the web crawler (formerly CRAWLER_BROWSER_ENABLED)."""
    if os.getenv("CRAWLER_WEB_SPA_ENABLED") is not None:
        return env_bool("CRAWLER_WEB_SPA_ENABLED", False)
    if os.getenv("CRAWLER_BROWSER_ENABLED") is not None:
        return config.CRAWLER_BROWSER_ENABLED
    return False


def infer_crawler_type(scrape_url: str) -> CrawlerType:
    if is_youtube_scrape_url(scrape_url):
        return CrawlerType.YOUTUBE
    return CrawlerType.WEB


def crawl_disabled_reason(scrape_url: str) -> str | None:
    ctype = infer_crawler_type(scrape_url)
    if not is_crawler_enabled(ctype):
        return f"crawler_{ctype}_disabled"

    needs_spa = uses_browser_engine(scrape_url)
    if needs_spa and not is_web_spa_enabled():
        return "crawler_web_spa_disabled"

    return None


def mail_crawl_disabled_reason() -> str | None:
    if is_crawler_enabled(CrawlerType.MAIL):
        return None
    return "crawler_mail_disabled"


def chain_crawl_disabled_reason() -> str | None:
    if is_crawler_enabled(CrawlerType.CHAIN):
        return None
    return "crawler_chain_disabled"


def metrics_crawl_disabled_reason() -> str | None:
    if is_crawler_enabled(CrawlerType.METRICS):
        return None
    return "crawler_metrics_disabled"


def list_crawler_status() -> list[dict[str, object]]:
    """Snapshot for ops/debug."""
    rows = load_crawler_config()
    out: list[dict[str, object]] = []
    for ctype in CrawlerType:
        row = rows.get(ctype.value)
        out.append(
            {
                "crawler_type": ctype.value,
                "display_name": row.display_name if row else ctype.value,
                "enabled_db": row.enabled if row else False,
                "enabled_effective": is_crawler_enabled(ctype),
                "web_spa": is_web_spa_enabled() if ctype == CrawlerType.WEB else None,
            }
        )
    return out
