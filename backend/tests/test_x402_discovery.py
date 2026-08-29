"""describe_json_endpoint.

The wrapper that makes the OutputConfig bug (both KYC routes, found
2026-08-29) structurally impossible to reproduce.
"""

from __future__ import annotations

import pytest

pytest.importorskip("x402")

from app.modules.x402.discovery import describe_json_endpoint


def test_describe_json_endpoint_never_passes_a_bare_output_dict() -> None:
    """The whole point of this wrapper: calling it can never reproduce the AttributeError a bare dict output={"example": ...} caused."""
    result = describe_json_endpoint(
        input={"wallet": "..."},
        input_schema={"properties": {"wallet": {"type": "string"}}},
        output_example={"ok": True},
    )
    assert "bazaar" in result


def test_describe_json_endpoint_with_no_output_example_does_not_raise() -> None:
    """output_example is optional -- omitting it must not raise, and must not pass a bare None where OutputConfig(example=None) would be wrong either."""
    result = describe_json_endpoint(input={}, input_schema={})
    assert "bazaar" in result


def test_describe_json_endpoint_body_type_json_declares_a_body_extension() -> None:
    """body_type="json" must produce a body-shaped declaration, not the query-params default -- this is what x402_directory's POST /x402/list relies on."""
    result = describe_json_endpoint(
        input={"url": "https://example.com"},
        input_schema={"type": "object"},
        output_example={"ok": True},
        body_type="json",
    )
    assert result["bazaar"]["info"]["input"]["body"] == {"url": "https://example.com"}
    assert result["bazaar"]["info"]["input"]["bodyType"] == "json"


def test_describe_json_endpoint_without_body_type_declares_a_query_extension() -> None:
    """No body_type (the GET/query-params default) must not produce a body-shaped declaration -- this is what kyc_verify's GET route relies on."""
    result = describe_json_endpoint(
        input={"wallet": "..."},
        input_schema={"properties": {"wallet": {"type": "string"}}},
        output_example={"ok": True},
    )
    assert result["bazaar"]["info"]["input"]["queryParams"] == {"wallet": "..."}
