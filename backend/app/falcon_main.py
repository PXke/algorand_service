"""Falcon app entrypoint."""

from __future__ import annotations

from typing import Any

import falcon

from app.core.config import settings
from app.core.cors import (
    ALLOW_CORS_METHODS,
    DEFAULT_CORS_HEADERS,
    origin_allowed,
)
from app.core.falcon_router import FalconRouter
from app.core.health import run_readiness_checks
from app.core.observability import init_bugsnag
from app.modules.admin.api.routes import register_admin_routes
from app.modules.auth.api.routes import register_auth_routes
from app.modules.contact.api.routes import register_contact_routes
from app.modules.glossary.api.routes import register_glossary_routes
from app.modules.ingest.api.routes import register_ingest_routes
from app.modules.kyc.api.routes import register_kyc_routes
from app.modules.media.api.routes import register_media_routes
from app.modules.metrics.api.routes import register_metrics_routes
from app.modules.news.api.routes import register_news_routes
from app.modules.placements.api.routes import register_placement_routes
from app.modules.registry.api.routes import register_registry_routes
from app.modules.search.api.routes import register_search_routes
from app.modules.seo.api.routes import register_seo_routes
from app.modules.sharing.api.routes import register_sharing_routes
from app.modules.suggestions.api.routes import register_suggestions_routes


class CorsMiddleware:
    """CORS handling equivalent to previous Robyn hooks."""

    def __init__(self, origins: list[str]) -> None:
        self._origins = origins

    def process_request(self, req: falcon.Request, resp: falcon.Response) -> None:
        # Falcon's headers mapping is uppercase; get_header is case-insensitive.
        origin = req.get_header("Origin")
        if origin and not origin_allowed(origin, self._origins):
            raise falcon.HTTPForbidden(title="", description="")

        if req.method == "OPTIONS":
            resp.status = falcon.HTTP_204
            resp.set_header("Access-Control-Allow-Methods", ALLOW_CORS_METHODS)
            resp.set_header("Access-Control-Allow-Headers", ", ".join(DEFAULT_CORS_HEADERS))
            resp.set_header("Access-Control-Max-Age", "3600")
            resp.set_header("Vary", "Origin")
            if origin:
                resp.set_header("Access-Control-Allow-Origin", origin)
                resp.set_header("Access-Control-Allow-Credentials", "true")
            resp.complete = True

    def process_response(
        self,
        req: falcon.Request,
        resp: falcon.Response,
        resource: Any,
        req_succeeded: bool,
    ) -> None:
        _ = resource, req_succeeded
        origin = req.get_header("Origin")
        if origin and origin_allowed(origin, self._origins):
            resp.set_header("Access-Control-Allow-Origin", origin)
            resp.set_header("Access-Control-Allow-Credentials", "true")
            resp.set_header("Vary", "Origin")


class ApiRobotsTagMiddleware:
    """Apply noindex header to API responses."""

    def process_response(
        self,
        req: falcon.Request,
        resp: falcon.Response,
        resource: Any,
        req_succeeded: bool,
    ) -> None:
        _ = resource, req_succeeded
        if req.path.startswith("/api/"):
            resp.set_header("X-Robots-Tag", "noindex")


class HealthResource:
    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        _ = req
        resp.media = {"status": "ok", "service": settings.app_name, "env": settings.app_env}


class HealthReadyResource:
    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        _ = req
        checks = run_readiness_checks()
        ok = all(check.ok for check in checks if check.name in {"redis", "cassandra"})
        resp.media = {
            "status": "ok" if ok else "degraded",
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
        }


def create_app() -> falcon.App:
    init_bugsnag(release_stage=settings.app_env or "prod")
    middleware: list[Any] = [ApiRobotsTagMiddleware()]
    if settings.cors_origins:
        middleware.insert(0, CorsMiddleware(settings.cors_origins))

    app = falcon.App(middleware=middleware)

    # Prefer msgspec for all resp.media / req.media JSON (Falcon recipe).
    from app.core.serialization import _decoder, _encoder

    json_handler = falcon.media.JSONHandler(dumps=_encoder.encode, loads=_decoder.decode)
    app.req_options.media_handlers[falcon.MEDIA_JSON] = json_handler
    app.resp_options.media_handlers[falcon.MEDIA_JSON] = json_handler

    app.add_route("/health", HealthResource())
    app.add_route("/health/ready", HealthReadyResource())

    router = FalconRouter(app)
    register_auth_routes(router)
    register_media_routes(router)
    register_metrics_routes(router)
    register_news_routes(router)
    register_placement_routes(router)
    register_ingest_routes(router)
    register_admin_routes(router)
    register_registry_routes(router)
    register_search_routes(router)
    register_contact_routes(router)
    register_glossary_routes(router)
    register_sharing_routes(router)
    if settings.suggestions_enabled:
        register_suggestions_routes(router)
    if settings.x402_enabled:
        register_kyc_routes(router)
    register_seo_routes(router)
    return app


app = create_app()
