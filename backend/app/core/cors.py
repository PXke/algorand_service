"""CORS origin policy shared by Falcon middleware and tests."""

from __future__ import annotations

from app.core.config import settings

# Custom headers the Flutter client sends: session auth (x-session-token),
# admin endpoints (x-admin-wallet), push ingest (x-ingest-key). x402 clients
# retry a 402 with PAYMENT-SIGNATURE; without it on the preflight allow-list
# a browser agent cannot complete a paid call from a web origin.
DEFAULT_CORS_HEADERS = [
    "Content-Type",
    "Authorization",
    "X-Session-Token",
    "X-Admin-Wallet",
    "X-Ingest-Key",
    "PAYMENT-SIGNATURE",
]
# 402 responses carry the offer in PAYMENT-REQUIRED; browsers hide unknown
# response headers from JS unless they are listed here.
DEFAULT_CORS_EXPOSE_HEADERS = [
    "PAYMENT-REQUIRED",
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
