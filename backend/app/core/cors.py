from __future__ import annotations

from robyn import Response, Robyn

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


def register_cors(app: Robyn) -> None:
    origins = settings.cors_origins
    if not origins:
        return

    @app.before_request()
    def cors_middleware(request):
        origin = request.headers.get("Origin")

        if origin and not _origin_allowed(origin, origins):
            return Response(status_code=403, description="", headers={})

        if request.method == "OPTIONS":
            allow_origin = origin if origin else (origins[0] if origins else "*")
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": allow_origin,
                    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS",
                    "Access-Control-Allow-Headers": ", ".join(_DEFAULT_HEADERS),
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Max-Age": "3600",
                },
                description="",
            )

        return request

    if len(origins) == 1:
        app.set_response_header("Access-Control-Allow-Origin", origins[0])
    else:
        app.set_response_header("Access-Control-Allow-Origin", "*")

    app.set_response_header(
        "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS"
    )
    app.set_response_header("Access-Control-Allow-Headers", ", ".join(_DEFAULT_HEADERS))
    app.set_response_header("Access-Control-Allow-Credentials", "true")
