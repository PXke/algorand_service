from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrawlerConfigRow:
    crawler_type: str
    display_name: str
    description: str
    enabled: bool


def _defaults() -> dict[str, CrawlerConfigRow]:
    return {
        "web": CrawlerConfigRow(
            crawler_type="web",
            display_name="Web (HTML + SPA)",
            description="HTTPS and optional Playwright.",
            enabled=True,
        ),
        "mail": CrawlerConfigRow(
            crawler_type="mail",
            display_name="Email (IMAP)",
            description="IMAP inbox poll.",
            enabled=True,
        ),
        "chain": CrawlerConfigRow(
            crawler_type="chain",
            display_name="On-chain",
            description="Chain tail transaction matching.",
            enabled=True,
        ),
        "metrics": CrawlerConfigRow(
            crawler_type="metrics",
            display_name="Market metrics",
            description="Price/TVL/node metrics for charts.",
            enabled=True,
        ),
    }


@lru_cache(maxsize=1)
def load_crawler_config() -> dict[str, CrawlerConfigRow]:
    """Load from Cassandra; fall back to in-code defaults if table missing."""
    merged = _defaults()
    try:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()
        rows = session.execute(
            """
            SELECT crawler_type, display_name, description, enabled
            FROM crawler_config
            """
        )
        for row in rows:
            ctype = str(row.crawler_type).strip().lower()
            if ctype not in merged:
                continue
            merged[ctype] = CrawlerConfigRow(
                crawler_type=ctype,
                display_name=str(row.display_name or merged[ctype].display_name),
                description=str(row.description or merged[ctype].description),
                enabled=bool(row.enabled),
            )
    except Exception as exc:
        logger.debug("crawler_config table unavailable, using defaults: %s", exc)
    return merged


def clear_crawler_config_cache() -> None:
    load_crawler_config.cache_clear()
