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

    @app.get("/api/v1/metrics/dashboard")
    async def metrics_dashboard(request: Request) -> dict:
        asset_id = request.query_params.get("asset_id", None)
        return serialization.to_builtins(dashboard.get_dashboard(asset_id=asset_id))
