"""PlatformError status mapping and the uniform JSON error shape."""

from __future__ import annotations

import pytest

from app.core.errors import PlatformError, http_status_for_code


def test_platform_error_carries_status() -> None:
    """A PlatformError preserves its code and explicit HTTP status."""
    exc = PlatformError("not_found", "missing", http_status=404)
    assert exc.code == "not_found"
    assert exc.http_status == 404


def test_http_status_mapping() -> None:
    """Known error codes map to their expected HTTP status, unknown codes fall back to 400."""
    assert http_status_for_code("duplicate_txid") == 409
    assert http_status_for_code("rate_limited") == 429
    assert http_status_for_code("unknown_code") == 400


def test_json_error_response_shape() -> None:
    """json_error_response produces a JSON body with the given code and message."""
    pytest.importorskip("robyn")
    from app.core.http_errors import json_error_response

    resp = json_error_response(400, "invalid_request", "bad input")
    assert resp.status_code == 400
    assert '"code": "invalid_request"' in resp.description
    assert '"message": "bad input"' in resp.description
