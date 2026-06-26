from __future__ import annotations

from app.modules.registry.services.registry_service import RegistryService


def register_registry_routes(app) -> None:
    registry_service = RegistryService()

    @app.get("/api/v1/registry/services")
    async def list_services(request) -> dict:
        seeds_only = (request.query_params.get("seeds_only", "") or "").lower() in {
            "1",
            "true",
            "yes",
        }
        items = registry_service.list_services(seeds_only=seeds_only)
        return {"items": [item.model_dump() for item in items]}
