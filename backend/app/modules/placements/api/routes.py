from __future__ import annotations

from robyn import Request

from app.modules.placements.services.placement_service import PlacementService


def register_placement_routes(app) -> None:
    service = PlacementService()

    @app.get("/api/v1/news/placements")
    async def list_placements(request: Request) -> dict:
        slot = request.query_params.get("slot", "") or None
        limit_param = request.query_params.get("limit", "")
        limit = int(limit_param) if limit_param.isdigit() else None
        items = service.list_feed_placements(slot=slot, limit=limit)
        return {"items": [item.model_dump() for item in items]}
