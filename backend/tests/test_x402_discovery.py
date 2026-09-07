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


def test_a_post_declared_without_body_type_fails_the_facilitator_validator() -> None:
    """A body-less POST (vote/follow/renew) still needs body_type="json".

    Found live 2026-09-05: the resource server injects the real HTTP method
    into the declaration at request time, and the query-params schema only
    admits GET/HEAD/DELETE -- so a POST declared without body_type emits an
    extension the facilitator's own validator rejects, and the route is
    silently never catalogued in the Bazaar. This pins the package behaviour
    both ways: the query shape fails for a POST, the body shape (with an
    empty example body) passes.
    """
    from types import SimpleNamespace

    from x402.extensions.bazaar import (
        bazaar_resource_server_extension,
        validate_discovery_extension,
    )

    post_context = SimpleNamespace(method="POST")

    as_query = bazaar_resource_server_extension.enrich_declaration(
        describe_json_endpoint(output_example={"ok": True})["bazaar"], post_context
    )
    assert validate_discovery_extension(as_query).valid is False

    as_body = bazaar_resource_server_extension.enrich_declaration(
        describe_json_endpoint(body_type="json", output_example={"ok": True})["bazaar"],
        post_context,
    )
    assert validate_discovery_extension(as_body).valid is True
    assert as_body["info"]["input"]["body"] == {}
