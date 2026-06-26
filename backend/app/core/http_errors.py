from __future__ import annotations

import json

from robyn import Response

from app.core.errors import PlatformError


def json_error_response(status: int, code: str, message: str) -> Response:
    return Response(
        status_code=status,
        headers={"Content-Type": "application/json"},
        description=json.dumps({"error": {"code": code, "message": message}}),
    )


def json_error_from_platform(exc: PlatformError) -> Response:
    return json_error_response(exc.http_status, exc.code, exc.message)
