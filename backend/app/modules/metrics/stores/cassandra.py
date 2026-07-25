"""Cassandra reads for stored price samples and briefs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StoredPriceSample:
    """One stored price sample row."""
    market_cap_usd: float | None
    volume_24h_usd: float | None


@dataclass(frozen=True)
class StoredPriceBrief:
    """A stored price-metrics brief (windowed stats snapshot)."""
    asset_id: str
    asset_name: str
    currency: str
    current_price_usd: float
    change_24h_pct: float | None
    market_cap_usd: float | None
    prepared_at: datetime | None
    sample_count_24h: int


def load_price_history(asset_id: str, *, limit: int = 200) -> list[tuple[int, float]]:
    """(epoch_seconds, price_usd) points, oldest first, for sparklines.

    Best-effort: any failure reads as no history, never an error.
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import PriceMetricsStmts

    try:
        rows = get_cassandra_session().execute(
            PriceMetricsStmts.PRICE_HISTORY, (asset_id.strip().lower(), limit)
        )
    except Exception:
        return []
    points = [
        (int(row.collected_at.timestamp()), float(row.price_usd))
        for row in rows
        if row.collected_at is not None and row.price_usd is not None
    ]
    points.sort()
    return points


def load_price_brief(asset_id: str) -> StoredPriceBrief | None:
    """Fetch the latest prepared price brief for an asset, or None if none exists."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import PriceMetricsStmts

    session = get_cassandra_session()
    row = session.execute(PriceMetricsStmts.GET_BRIEF, (asset_id.strip().lower(),)).one()
    if row is None:
        return None

    market_cap: float | None = None
    sample_row = session.execute(
        PriceMetricsStmts.SAMPLE_MARKET_CAP, (asset_id.strip().lower(),)
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
    """Return the most recently collected price sample for an asset, or None."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import PriceMetricsStmts

    session = get_cassandra_session()
    rows = session.execute(PriceMetricsStmts.LATEST_SAMPLES, (asset_id.strip().lower(),))
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
        market_cap_usd=float(latest.market_cap_usd) if latest.market_cap_usd is not None else None,
        volume_24h_usd=float(latest.volume_24h_usd) if latest.volume_24h_usd is not None else None,
    )
