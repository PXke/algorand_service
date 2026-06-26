from __future__ import annotations

from app.core.config import settings
from app.modules.metrics.models.schemas import PriceMetricsResponse
from app.modules.metrics.stores.cassandra import load_latest_price_sample, load_price_brief


class PriceMetricsService:
    def get_spot(self, *, asset_id: str | None = None) -> PriceMetricsResponse:
        aid = (asset_id or settings.price_metrics_asset_id).strip().lower()
        brief = load_price_brief(aid)
        if brief is None:
            return PriceMetricsResponse(
                asset_id=aid,
                asset_name=aid,
                currency="USD",
                price_usd=0.0,
                change_24h_pct=None,
                prepared_at_epoch=None,
                sample_count_24h=0,
                available=False,
            )
        prepared_epoch = int(brief.prepared_at.timestamp()) if brief.prepared_at else None
        sample = load_latest_price_sample(aid)
        volume = sample.volume_24h_usd if sample else None
        market_cap = brief.market_cap_usd or (sample.market_cap_usd if sample else None)
        return PriceMetricsResponse(
            asset_id=brief.asset_id,
            asset_name=brief.asset_name,
            currency=brief.currency,
            price_usd=brief.current_price_usd,
            change_24h_pct=brief.change_24h_pct,
            market_cap_usd=market_cap,
            volume_24h_usd=volume,
            prepared_at_epoch=prepared_epoch,
            sample_count_24h=brief.sample_count_24h,
            available=True,
        )
