from __future__ import annotations

from robyn import Request

from app.core import serialization
from app.modules.metrics.services.dashboard_service import MetricsDashboardService
from app.modules.metrics.services.price_service import PriceMetricsService


def register_metrics_routes(app) -> None:
    service = PriceMetricsService()
    dashboard = MetricsDashboardService()

    @app.get("/api/v1/metrics/price")
    async def price_metrics(request: Request) -> dict:
        asset_id = request.query_params.get("asset_id", None)
        return serialization.to_builtins(service.get_spot(asset_id=asset_id))

    @app.get("/api/v1/metrics/price/history")
    async def price_history(request: Request) -> dict:
        """~Hourly (epoch, price) points for the front-page sparkline. Cached:
        the sampler writes about once an hour, so minutes-stale is invisible."""
        from app.core.cache import cached_json
        from app.core.config import settings
        from app.modules.metrics.stores.cassandra import load_price_history

        asset_id = (
            request.query_params.get("asset_id", None) or settings.price_metrics_asset_id
        ).strip().lower()

        def compute() -> dict:
            points = load_price_history(asset_id)
            return {
                "asset_id": asset_id,
                "points": [{"epoch": e, "price_usd": p} for e, p in points],
            }

        return cached_json(f"metrics:price-history:{asset_id}", 300, compute)

    @app.get("/api/v1/metrics/dashboard")
    async def metrics_dashboard(request: Request) -> dict:
        asset_id = request.query_params.get("asset_id", None)
        return serialization.to_builtins(dashboard.get_dashboard(asset_id=asset_id))
