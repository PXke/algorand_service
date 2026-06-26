from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import PRICE_METRICS_SAMPLE_LIMIT
from app.modules.metrics.price_metrics_models import PriceMetricsBrief, PriceSampleRow, PriceTick


def insert_sample(tick: PriceTick) -> None:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    session.execute(
        """
        INSERT INTO price_metric_samples (
          asset_id, collected_at, price_usd, currency,
          change_24h_pct, market_cap_usd, volume_24h_usd, source
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) USING TTL 31536000
        """,
        (
            tick.asset_id,
            tick.collected_at,
            tick.price_usd,
            tick.currency,
            tick.change_24h_pct,
            tick.market_cap_usd,
            tick.volume_24h_usd,
            tick.source,
        ),
    )


def list_recent_samples(
    asset_id: str,
    *,
    lookback_days: int = 7,
    limit: int | None = None,
) -> list[PriceSampleRow]:
    from app.core.cassandra import get_cassandra_session

    cap = limit if limit is not None else PRICE_METRICS_SAMPLE_LIMIT
    cutoff = datetime.now(tz=UTC) - timedelta(days=lookback_days)
    session = get_cassandra_session()
    rows = session.execute(
        """
        SELECT asset_id, collected_at, price_usd, currency,
               change_24h_pct, market_cap_usd, volume_24h_usd, source
        FROM price_metric_samples
        WHERE asset_id = %s AND collected_at >= %s
        LIMIT %s
        """,
        (asset_id.strip().lower(), cutoff, cap),
    )
    items: list[PriceSampleRow] = []
    for row in rows:
        items.append(
            PriceSampleRow(
                asset_id=row.asset_id,
                collected_at=row.collected_at,
                price_usd=float(row.price_usd),
                currency=row.currency or "USD",
                change_24h_pct=row.change_24h_pct,
                market_cap_usd=row.market_cap_usd,
                volume_24h_usd=row.volume_24h_usd,
                source=row.source or "coingecko",
            )
        )
    items.sort(key=lambda item: item.collected_at)
    return items


def save_brief(brief: PriceMetricsBrief) -> None:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    session.execute(
        """
        INSERT INTO price_metrics_brief (
          asset_id, prepared_at, asset_name, currency, current_price_usd,
          change_24h_pct, sample_count_24h, sample_count_7d, mistral_context
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            brief.asset_id,
            brief.prepared_at,
            brief.asset_name,
            brief.currency,
            brief.current_price_usd,
            brief.change_24h_pct,
            brief.sample_count_24h,
            brief.sample_count_7d,
            brief.mistral_context,
        ),
    )


def load_brief(asset_id: str) -> PriceMetricsBrief | None:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    row = session.execute(
        """
        SELECT asset_id, prepared_at, asset_name, currency, current_price_usd,
               change_24h_pct, sample_count_24h, sample_count_7d, mistral_context
        FROM price_metrics_brief
        WHERE asset_id = %s
        """,
        (asset_id.strip().lower(),),
    ).one()
    if row is None:
        return None
    return PriceMetricsBrief(
        asset_id=row.asset_id,
        asset_name=row.asset_name or row.asset_id,
        currency=row.currency or "USD",
        prepared_at=row.prepared_at,
        current_price_usd=float(row.current_price_usd),
        change_24h_pct=row.change_24h_pct,
        sample_count_24h=int(row.sample_count_24h or 0),
        sample_count_7d=int(row.sample_count_7d or 0),
        mistral_context=row.mistral_context or "",
    )


def load_mistral_context(asset_id: str) -> str:
    """Prepared price narrative for Mistral prompts; empty when not yet collected."""
    brief = load_brief(asset_id)
    if brief is None or not brief.mistral_context.strip():
        return ""
    return brief.mistral_context.strip()
