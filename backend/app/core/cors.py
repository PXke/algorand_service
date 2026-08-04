"""CORS origin policy shared by Falcon middleware and tests."""

from __future__ import annotations

from app.core.config import settings

# Custom headers the Flutter client sends: session auth (x-session-token),
# admin endpoints (x-admin-wallet), push ingest (x-ingest-key).
DEFAULT_CORS_HEADERS = [
    "Content-Type",
    "Authorization",
    "X-Session-Token",
    "X-Admin-Wallet",
    "X-Ingest-Key",
]
ALLOW_CORS_METHODS = "GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS"
_LOCAL_DEV_ENVS = frozenset({"dev", "test"})


def cors_permissive() -> bool:
    """When true, any Origin is accepted (local dev: Flutter web, Electron renderer, etc.)."""
    if settings.cors_permissive is not None:
        return settings.cors_permissive
    return settings.app_env in _LOCAL_DEV_ENVS


def origin_allowed(origin: str, allowed: list[str]) -> bool:
    if "*" in allowed or origin in allowed:
        return True
    return cors_permissive()


def register_cors(app: object) -> None:
    """Deprecated shim; CORS is implemented in Falcon middleware."""
    _ = app
