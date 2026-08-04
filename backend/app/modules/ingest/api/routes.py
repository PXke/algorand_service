"""HTTP routes for pushing external ingest signals into the pipeline."""

from __future__ import annotations

import secrets

import msgspec

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.modules.ingest.queue import push_signal
from app.modules.ingest.schemas import IngestSignalRequest


def _check_ingest_auth(request: Request) -> Response | None:
    if not settings.ingest_api_key:
        return json_error_response(
            503,
            "ingest_disabled",
            "INGEST_API_KEY is not configured on the API",
        )
    header = request.headers.get("x-ingest-key") or request.headers.get("X-Ingest-Key")
    auth = request.headers.get("authorization") or ""
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    provided = (header or token or "").strip()
    # Constant-time compare: a plain != leaks the key byte-by-byte via response
    # timing (short-circuits at the first differing char).
    if not provided or not secrets.compare_digest(provided, settings.ingest_api_key):
        return json_error_response(401, "unauthorized", "Invalid ingest API key")
    return None


def register_ingest_routes(app: Router) -> None:
    """Register the external ingest-signal endpoint on the app."""

    @app.post("/api/v1/ingest/signal")
    def ingest_signal(request: Request) -> Response:
        denied = _check_ingest_auth(request)
        if denied is not None:
            return denied

        try:
            body = serialization.loads(request.body or b"{}")
        except Exception:
            return json_error_response(400, "invalid_json", "Request body must be JSON")

        if not isinstance(body, dict):
            return json_error_response(400, "invalid_request", "JSON object required")

        try:
            payload = msgspec.convert(body, IngestSignalRequest, strict=False)
        except Exception as exc:
            return json_error_response(400, "validation_error", str(exc))

        depth = push_signal(serialization.to_builtins(payload))
        return {
            "status": "queued",
            "queue": "algorand:ingest:external_signals",
            "depth": depth,
        }
