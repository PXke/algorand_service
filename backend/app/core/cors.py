"""CORS: per-request Origin reflection, not a static allow-list header."""

from __future__ import annotations

from robyn import Request, Response, Robyn

from app.core.config import settings

# Custom headers the Flutter client sends: session auth (x-session-token),
# admin endpoints (x-admin-wallet), push ingest (x-ingest-key).
_DEFAULT_HEADERS = [
    "Content-Type",
    "Authorization",
    "X-Session-Token",
    "X-Admin-Wallet",
    "X-Ingest-Key",
]
_LOCAL_DEV_ENVS = frozenset({"dev", "test"})


def cors_permissive() -> bool:
    """When true, any Origin is accepted (local dev: Flutter web, Electron renderer, etc.)."""
    if settings.cors_permissive is not None:
        return settings.cors_permissive
    return settings.app_env in _LOCAL_DEV_ENVS


def _origin_allowed(origin: str, allowed: list[str]) -> bool:
    if "*" in allowed or origin in allowed:
        return True
    return cors_permissive()


_ALLOW_METHODS = "GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS"


def register_cors(app: Robyn) -> None:
    """Install before/after-request hooks that enforce and reflect the configured CORS origins."""
    origins = settings.cors_origins
    if not origins:
        return

    @app.before_request()
    def cors_middleware(request: Request) -> Request | Response:
        origin = request.headers.get("Origin")

        if origin and not _origin_allowed(origin, origins):
            return Response(status_code=403, description="", headers={})

        if request.method == "OPTIONS":
            # Reflect the (already-validated) origin. Never echo "*" here: it is
            # invalid combined with Allow-Credentials, and a preflight only ever
            # arrives with a concrete Origin anyway.
            headers = {
                "Access-Control-Allow-Methods": _ALLOW_METHODS,
                "Access-Control-Allow-Headers": ", ".join(_DEFAULT_HEADERS),
                "Access-Control-Max-Age": "3600",
                "Vary": "Origin",
            }
            if origin:
                headers["Access-Control-Allow-Origin"] = origin
                headers["Access-Control-Allow-Credentials"] = "true"
            return Response(status_code=204, headers=headers, description="")

        return request

    @app.after_request()
    def cors_response_headers(request: Request, response: Response) -> Response:
        # Set Allow-Origin per-response by REFLECTING the validated request
        # origin, rather than a static header. A static value cannot vary with
        # >1 configured origin, and the old fallback emitted
        # "Access-Control-Allow-Origin: *" together with Allow-Credentials:true
        # — a combination browsers reject and that would, if it ever resolved,
        # expose credentialed responses to any site. Disallowed origins were
        # already 403'd in before_request; here we only reach allowed ones.
        origin = request.headers.get("Origin")
        if origin and _origin_allowed(origin, origins):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        return response
