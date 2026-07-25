"""Uniform JSON error-response shape for the HTTP layer."""

from __future__ import annotations

import json

from robyn import Response

from app.core.errors import PlatformError


def json_error_response(status: int, code: str, message: str) -> Response:
    """Build a Robyn Response with a uniform JSON error body."""
    return Response(
        status_code=status,
        headers={"Content-Type": "application/json"},
        description=json.dumps({"error": {"code": code, "message": message}}),
    )


def json_error_from_platform(exc: PlatformError) -> Response:
    """Convert a PlatformError into its equivalent JSON error Response."""
    return json_error_response(exc.http_status, exc.code, exc.message)
