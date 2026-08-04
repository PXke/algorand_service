"""HTTP routes for sponsored/curated feed placements."""

from __future__ import annotations

from app.core import serialization
from app.core.http import Request, Router
from app.core.query_params import query_param
from app.modules.placements.services.placement_service import PlacementService


def register_placement_routes(app: Router) -> None:
    """Register the sponsored/curated feed placements endpoint on the app."""
    service = PlacementService()

    @app.get("/api/v1/news/placements")
    def list_placements(request: Request) -> dict:
        slot = query_param(request.query_params.get("slot", "")) or None
        limit_param = query_param(request.query_params.get("limit", ""))
        limit = int(limit_param) if limit_param.isdigit() else None
        items = service.list_feed_placements(slot=slot, limit=limit)
        return {"items": serialization.to_builtins(items)}
