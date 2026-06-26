from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StoredPriceSample:
    market_cap_usd: float | None
    volume_24h_usd: float | None


@dataclass(frozen=True)
class StoredPriceBrief:
    asset_id: str
    asset_name: str
    currency: str
    current_price_usd: float
    change_24h_pct: float | None
    market_cap_usd: float | None
    prepared_at: datetime | None
    sample_count_24h: int


def load_price_brief(asset_id: str) -> StoredPriceBrief | None:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    row = session.execute(
        """
        SELECT asset_id, asset_name, currency, current_price_usd, change_24h_pct,
               sample_count_24h, prepared_at
        FROM price_metrics_brief
        WHERE asset_id = %s
        """,
        (asset_id.strip().lower(),),
    ).one()
    if row is None:
        return None

    market_cap: float | None = None
    sample_row = session.execute(
        """
        SELECT market_cap_usd FROM price_metric_samples
        WHERE asset_id = %s
        LIMIT 1
        """,
        (asset_id.strip().lower(),),
    ).one()
    if sample_row is not None and sample_row.market_cap_usd is not None:
        market_cap = float(sample_row.market_cap_usd)

    return StoredPriceBrief(
        asset_id=row.asset_id,
        asset_name=row.asset_name or row.asset_id,
        currency=row.currency or "USD",
        current_price_usd=float(row.current_price_usd),
        change_24h_pct=row.change_24h_pct,
        market_cap_usd=market_cap,
        prepared_at=row.prepared_at,
        sample_count_24h=int(row.sample_count_24h or 0),
    )


def load_latest_price_sample(asset_id: str) -> StoredPriceSample | None:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    rows = session.execute(
        """
        SELECT market_cap_usd, volume_24h_usd, collected_at
        FROM price_metric_samples
        WHERE asset_id = %s
        LIMIT 20
        """,
        (asset_id.strip().lower(),),
    )
    latest = None
    latest_at = None
    for row in rows:
        collected = row.collected_at
        if collected is None:
            continue
        if latest_at is None or collected > latest_at:
            latest_at = collected
            latest = row
    if latest is None:
        return None
    return StoredPriceSample(
        market_cap_usd=float(latest.market_cap_usd)
        if latest.market_cap_usd is not None
        else None,
        volume_24h_usd=float(latest.volume_24h_usd)
        if latest.volume_24h_usd is not None
        else None,
    )
