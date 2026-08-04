"""HTTP routes for the service registry."""

from __future__ import annotations

from app.core import serialization
from app.core.http import Request, Router
from app.modules.registry.services.registry_service import RegistryService


def register_registry_routes(app: Router) -> None:
    """Register the service-registry HTTP routes on the Robyn app."""
    registry_service = RegistryService()

    @app.get("/api/v1/registry/services")
    def list_services(request: Request) -> dict:
        seeds_only = (request.query_params.get("seeds_only", "") or "").lower() in {
            "1",
            "true",
            "yes",
        }
        items = registry_service.list_services(seeds_only=seeds_only)
        return {"items": serialization.to_builtins(items)}
