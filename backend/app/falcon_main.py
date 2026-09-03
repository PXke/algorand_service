"""Falcon app entrypoint."""

from __future__ import annotations

from typing import Any

import falcon

from app.core.config import settings
from app.core.cors import (
    ALLOW_CORS_METHODS,
    DEFAULT_CORS_EXPOSE_HEADERS,
    DEFAULT_CORS_HEADERS,
    origin_allowed,
)
from app.core.falcon_router import FalconRouter
from app.core.health import run_readiness_checks
from app.core.observability import init_bugsnag
from app.modules.admin.api.routes import register_admin_routes
from app.modules.auth.api.routes import register_auth_routes
from app.modules.chain.algod_proxy import register_algod_proxy
from app.modules.contact.api.routes import register_contact_routes
from app.modules.glossary.api.routes import register_glossary_routes
from app.modules.ingest.api.routes import register_ingest_routes
from app.modules.kya.api.routes import register_kya_routes
from app.modules.media.api.routes import register_media_routes
from app.modules.metrics.api.routes import register_metrics_routes
from app.modules.news.api.routes import register_news_routes
from app.modules.placements.api.routes import register_placement_routes
from app.modules.registry.api.routes import register_registry_routes
from app.modules.search.api.routes import register_search_routes
from app.modules.seo.api.routes import register_seo_routes
from app.modules.sharing.api.routes import register_sharing_routes
from app.modules.suggestions.api.routes import register_suggestions_routes
from app.modules.x402_board.api.routes import register_x402_board_routes
from app.modules.x402_catalog.api.routes import register_x402_catalog_routes
from app.modules.x402_directory.api.routes import register_x402_directory_routes
from app.modules.x402_features.api.routes import register_x402_features_routes
from app.modules.x402_grading.api.routes import register_x402_grading_routes
from app.modules.x402_news.api.routes import register_x402_news_routes
from app.modules.x402_receipts.api.routes import register_x402_receipts_routes
from app.modules.x402_scan.api.routes import register_x402_scan_routes
from app.modules.x402_social.api.routes import register_x402_social_routes
from app.modules.x402_wellknown.api.routes import register_x402_wellknown_routes


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
            resp.set_header("Access-Control-Expose-Headers", ", ".join(DEFAULT_CORS_EXPOSE_HEADERS))
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
        # Public: name + ok only. Check `detail` can contain exception strings
        # (Redis URLs, Cassandra hosts) and queue depths — those stay on the
        # admin health-check route.
        resp.media = {
            "status": "ok" if ok else "degraded",
            "checks": [{"name": c.name, "ok": c.ok} for c in checks],
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
    register_algod_proxy(app)
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
        _register_x402_routes(router)
    register_seo_routes(router)
    return app


def _register_x402_routes(router: FalconRouter) -> None:
    """Register every x402 product route, each gated a second time on its own store setting.

    "memory" is a per-process dict (CLAUDE.md section 9: memory backend is
    dev/test only), and under gunicorn's multiple worker processes a paid
    write in one worker is invisible to a read that lands on another.
    Registering a paid route backed by memory in a x402_enabled deployment
    would accept real settled payments with no reliable way to honor what
    was paid for -- so a product stays unregistered (a clean 404, nothing
    charged) until its own store is explicitly set to something durable,
    rather than going live silently alongside whichever products actually
    are. Split out of create_app to keep that function's branching bounded.
    """
    if settings.kyc_store != "memory":
        register_kya_routes(router)
    if settings.x402_directory_store != "memory":
        register_x402_directory_routes(router)
    if settings.x402_board_store != "memory":
        register_x402_board_routes(router)
    if settings.x402_features_store != "memory":
        register_x402_features_routes(router)
    if settings.x402_grading_store != "memory":
        register_x402_grading_routes(router)
    # The News Engine has no store of its own: it reads through the news
    # module's store, so its durability gate is the news store's setting.
    if settings.news_store != "memory":
        register_x402_news_routes(router)
    # Prototype, off by default -- see modules/x402_scan/__init__.py for the
    # two owner decisions (which host runs the sandbox container engine;
    # whether/when to add dynamic analysis) still outstanding before this is
    # safe to flip on for real traffic.
    if settings.x402_scan_enabled:
        register_x402_scan_routes(router)
    # Phase S0 only (identity/foundation layer) -- see
    # docs/x402-social-design.md sections 1, 7 and app/modules/x402_social/.
    # Same "memory" gate as every other product: a paid write against a
    # per-process dict is invisible across gunicorn workers, so this stays
    # unregistered (clean 404, nothing charged) until the store is flipped
    # to something durable.
    if settings.x402_social_store != "memory":
        register_x402_social_routes(router)
    # Signed fulfillment receipts (docs/x402-execution-trust-evaluation.md
    # item 1): the free read side, GET /api/v1/x402/receipts/{receipt_id}.
    # Generation itself (modules/x402/receipts.py, hooked into every
    # run_with_refund caller above) is gated separately, on whether
    # x402_receipt_signing_mnemonic is configured -- this gate is only
    # about whether the READ route is reachable, same "memory" reasoning as
    # every other product store above (a receipt written to a per-process
    # dict would be invisible to a read landing on another gunicorn worker).
    if settings.x402_receipts_store != "memory":
        register_x402_receipts_routes(router)
    # The catalog lists whichever of the products above were registered (it
    # re-evaluates the same gates), so it comes last and is gated only on
    # the shared switch.
    register_x402_catalog_routes(router)
    # /.well-known/x402 and /openapi.json are meta/bootstrap routes, like the
    # catalog itself: no product store gate of their own, they just reshape
    # whatever register_x402_catalog_routes's build_catalog() already produced.
    register_x402_wellknown_routes(router)


app = create_app()
