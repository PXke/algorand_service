from __future__ import annotations

from app.core.config import settings
from app.modules.metrics.models.dashboard_schemas import MetricsDashboardResponse, MetricTile
from app.modules.metrics.services.network_service import fetch_algod_status
from app.modules.metrics.services.price_service import PriceMetricsService
from app.modules.metrics.stores.cassandra import load_latest_price_sample
from app.modules.news.services.news_service import NewsService


def _fmt_usd(amount: float | None) -> str | None:
    if amount is None or amount <= 0:
        return None
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.1f}K"
    return f"${amount:.2f}"


def _fmt_int(value: int | None) -> str | None:
    if value is None:
        return None
    return f"{value:,}"


class MetricsDashboardService:
    def __init__(
        self,
        *,
        price_service: PriceMetricsService | None = None,
        news_service: NewsService | None = None,
    ) -> None:
        self._price = price_service or PriceMetricsService()
        self._news = news_service or NewsService()

    def get_dashboard(self, *, asset_id: str | None = None) -> MetricsDashboardResponse:
        aid = (asset_id or settings.price_metrics_asset_id).strip().lower()
        tiles: list[MetricTile] = []

        spot = self._price.get_spot(asset_id=aid)
        sample = load_latest_price_sample(aid)

        if spot.available:
            change = spot.change_24h_pct
            hint = None
            if change is not None:
                sign = "+" if change > 0 else ""
                hint = f"{sign}{change:.2f}% 24h"
            tiles.append(
                MetricTile(
                    id="algo_price",
                    label="ALGO",
                    value=f"${spot.price_usd:.4f}" if spot.price_usd < 1 else f"${spot.price_usd:,.2f}",
                    hint=hint,
                    available=True,
                )
            )
        else:
            tiles.append(
                MetricTile(
                    id="algo_price",
                    label="ALGO",
                    value="—",
                    hint="Run price metrics",
                    available=False,
                )
            )

        vol = sample.volume_24h_usd if sample else None
        vol_text = _fmt_usd(vol)
        tiles.append(
            MetricTile(
                id="volume_24h",
                label="24h volume",
                value=vol_text or "—",
                hint="CoinGecko" if vol_text else None,
                available=vol_text is not None,
            )
        )

        cap = spot.market_cap_usd or (sample.market_cap_usd if sample else None)
        cap_text = _fmt_usd(cap)
        tiles.append(
            MetricTile(
                id="market_cap",
                label="Market cap",
                value=cap_text or "—",
                available=cap_text is not None,
            )
        )

        status = fetch_algod_status()
        last_round = status.get("last-round", status.get("LastRound"))
        if isinstance(last_round, int):
            tiles.append(
                MetricTile(
                    id="last_round",
                    label="Last round",
                    value=_fmt_int(last_round) or str(last_round),
                    hint="Algod",
                    available=True,
                )
            )
        else:
            tiles.append(
                MetricTile(
                    id="last_round",
                    label="Last round",
                    value="—",
                    hint="Algod unavailable",
                    available=False,
                )
            )

        catchup = status.get("catchup-time", status.get("CatchupTime"))
        time_since = status.get("time-since-last-round", status.get("TimeSinceLastRound"))
        if isinstance(time_since, int) and time_since >= 0:
            tiles.append(
                MetricTile(
                    id="round_latency",
                    label="Round time",
                    value=f"{time_since / 1_000_000_000:.1f}s",
                    hint="Since last block",
                    available=True,
                )
            )
        elif catchup is not None:
            tiles.append(
                MetricTile(
                    id="round_latency",
                    label="Round time",
                    value=str(catchup),
                    available=True,
                )
            )
        else:
            tiles.append(
                MetricTile(
                    id="round_latency",
                    label="Round time",
                    value="—",
                    available=False,
                )
            )

        try:
            article_count = self._news.count_feed()
            tiles.append(
                MetricTile(
                    id="articles",
                    label="Feed articles",
                    value=_fmt_int(article_count) or "0",
                    available=True,
                )
            )
        except Exception:
            tiles.append(
                MetricTile(
                    id="articles",
                    label="Feed articles",
                    value="—",
                    available=False,
                )
            )

        tiles.append(
            MetricTile(
                id="dex_volume",
                label="DEX volume",
                value="Soon",
                hint="Aggregators planned",
                available=False,
            )
        )

        return MetricsDashboardResponse(tiles=tiles)
