from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import (
    PRICE_ANALYSIS_ASSET_ID,
    WEEKLY_DIGEST_FEED_SCAN_LIMIT,
    WEEKLY_DIGEST_INCLUDE_FEED,
    WEEKLY_DIGEST_LOOKBACK_DAYS,
    WEEKLY_DIGEST_MAX_ARTICLES,
)
from app.modules.newspaper.price_analysis import (
    PriceAnalysisError,
    WeeklyPriceSnapshot,
    fetch_weekly_price,
)


@dataclass(frozen=True)
class DigestArticleItem:
    article_id: str
    service_id: str
    title: str
    summary: str
    published_at_epoch: int


@dataclass(frozen=True)
class WeeklyDigestContext:
    week_key: str
    week_label: str
    price: WeeklyPriceSnapshot
    articles: tuple[DigestArticleItem, ...]


def weekly_digest_trigger_id(week_key: str) -> str:
    return f"weekly-digest-{week_key}"


def digest_article_id(week_key: str) -> uuid.UUID:
    """Stable id so we do not publish two digests for the same ISO week."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"algorand-platform/weekly-digest/{week_key}")


def current_week_key(when: datetime | None = None) -> str:
    moment = when or datetime.now(tz=UTC)
    return moment.strftime("%Y-W%V")


def build_weekly_digest(
    *,
    asset_id: str = PRICE_ANALYSIS_ASSET_ID,
    include_feed: bool = WEEKLY_DIGEST_INCLUDE_FEED,
    lookback_days: int = WEEKLY_DIGEST_LOOKBACK_DAYS,
    max_articles: int = WEEKLY_DIGEST_MAX_ARTICLES,
    digest_service_ids: tuple[str, ...] = ("weekly-digest", "weekly-price-analysis"),
) -> WeeklyDigestContext:
    price = fetch_weekly_price(asset_id)
    week_key = current_week_key(price.as_of)
    articles: tuple[DigestArticleItem, ...] = ()
    if include_feed:
        articles = tuple(
            collect_recent_feed_articles(
                lookback_days=lookback_days,
                max_articles=max_articles,
                exclude_service_ids=digest_service_ids,
            )
        )
    return WeeklyDigestContext(
        week_key=week_key,
        week_label=price.as_of.strftime("%Y-%m-%d"),
        price=price,
        articles=articles,
    )


def collect_recent_feed_articles(
    *,
    lookback_days: int,
    max_articles: int,
    exclude_service_ids: tuple[str, ...],
) -> list[DigestArticleItem]:
    from app.modules.newspaper.article_store import list_feed_articles

    cutoff_epoch = int((datetime.now(tz=UTC) - timedelta(days=lookback_days)).timestamp())
    exclude = set(exclude_service_ids)
    rows = list_feed_articles(limit=WEEKLY_DIGEST_FEED_SCAN_LIMIT)
    recent = [
        DigestArticleItem(
            article_id=row.article_id,
            service_id=row.service_id,
            title=row.title,
            summary=row.summary,
            published_at_epoch=row.published_at_epoch,
        )
        for row in rows
        if row.published_at_epoch >= cutoff_epoch and row.service_id not in exclude
    ]
    recent.sort(key=lambda item: item.published_at_epoch, reverse=True)
    return recent[:max_articles]


def fetch_price_only(asset_id: str) -> WeeklyPriceSnapshot:
    """Backward-compatible price fetch."""
    return fetch_weekly_price(asset_id)


__all__ = [
    "DigestArticleItem",
    "PriceAnalysisError",
    "WeeklyDigestContext",
    "build_weekly_digest",
    "current_week_key",
    "digest_article_id",
    "fetch_weekly_price",
    "weekly_digest_trigger_id",
]
