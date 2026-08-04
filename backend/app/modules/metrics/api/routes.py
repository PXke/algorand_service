"""HTTP routes for the price-metrics dashboard."""

from __future__ import annotations

from app.core import serialization
from app.core.http import Request, Router
from app.core.query_params import query_param
from app.modules.metrics.services.dashboard_service import MetricsDashboardService
from app.modules.metrics.services.price_service import PriceMetricsService


def register_metrics_routes(app: Router) -> None:
    """Attach the price-metrics dashboard endpoints to the Robyn app."""
    service = PriceMetricsService()
    dashboard = MetricsDashboardService()

    @app.get("/api/v1/metrics/price")
    def price_metrics(request: Request) -> dict:
        asset_id = query_param(request.query_params.get("asset_id", "")) or None
        return serialization.to_builtins(service.get_spot(asset_id=asset_id))

    @app.get("/api/v1/metrics/price/history")
    def price_history(request: Request) -> dict:
        """~Hourly (epoch, price) points for the front-page sparkline.

        Cached: the sampler writes about once an hour, so minutes-stale is invisible.
        """
        from app.core.cache import cached_json
        from app.core.config import settings
        from app.modules.metrics.stores.cassandra import load_price_history

        asset_id = (
            query_param(request.query_params.get("asset_id", settings.price_metrics_asset_id))
            .lower()
        )

        def compute() -> dict:
            points = load_price_history(asset_id)
            return {
                "asset_id": asset_id,
                "points": [{"epoch": e, "price_usd": p} for e, p in points],
            }

        return cached_json(f"metrics:price-history:{asset_id}", 300, compute)

    @app.get("/api/v1/metrics/dashboard")
    def metrics_dashboard(request: Request) -> dict:
        asset_id = query_param(request.query_params.get("asset_id", "")) or None
        return serialization.to_builtins(dashboard.get_dashboard(asset_id=asset_id))
