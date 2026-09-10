"""Free methods: right URL, right params, no payment machinery touched at all."""

from __future__ import annotations

import pytest
from pxke_x402 import PxkeClient
from pxke_x402.client import BASE_URL
from pxke_x402.exceptions import PxkeHTTPError

from .conftest import FakeResponse, FakeSession


def test_catalog_hits_the_root_route() -> None:
    session = FakeSession([FakeResponse(200, {"name": "PXke x402 marketplace"})])
    client = PxkeClient(session=session)

    result = client.catalog()

    assert result == {"name": "PXke x402 marketplace"}
    assert session.calls[0].method == "GET"
    assert session.calls[0].url == f"{BASE_URL}/api/v1/x402"
    assert session.calls[0].params is None


def test_news_passes_tag_and_limit_and_drops_unset_ones() -> None:
    session = FakeSession([FakeResponse(200, {"items": []})])
    client = PxkeClient(session=session)

    client.news(tag="algorand")

    call = session.calls[0]
    assert call.url == f"{BASE_URL}/api/v1/x402/news"
    assert call.params == {"tag": "algorand"}  # limit omitted, not "limit": None


def test_a_4xx_on_a_free_route_raises_pxke_http_error_with_the_server_message() -> None:
    session = FakeSession(
        [FakeResponse(400, {"error": {"code": "bad_request", "message": "limit must be 1-100"}})]
    )
    client = PxkeClient(session=session)

    with pytest.raises(PxkeHTTPError) as excinfo:
        client.news(limit=0)

    assert excinfo.value.status_code == 400
    assert "limit must be 1-100" in str(excinfo.value)
    assert excinfo.value.body == {
        "error": {"code": "bad_request", "message": "limit must be 1-100"}
    }


def test_all_read_only_free_methods_hit_their_documented_routes() -> None:
    # One call each: news / settlements_recent (catalog and read_article have their own tests).
    routes_and_calls = [
        (lambda c: c.news(tag="algorand"), "GET", "/api/v1/x402/news"),
        (lambda c: c.settlements_recent(limit=5), "GET", "/api/v1/x402/settlements/recent"),
    ]
    for make_call, method, path in routes_and_calls:
        session = FakeSession([FakeResponse(200, {"items": []})])
        client = PxkeClient(session=session)

        make_call(client)

        assert session.calls[0].method == method
        assert session.calls[0].url == f"{BASE_URL}{path}"


def test_settlements_recent_sends_limit_as_a_query_param() -> None:
    session = FakeSession([FakeResponse(200, {"items": []})])
    client = PxkeClient(session=session)

    client.settlements_recent(limit=5)

    assert session.calls[0].params == {"limit": 5}


def test_read_article_is_free_and_url_encodes_the_article_id() -> None:
    """The article route carries no payment gate: a plain 200, no 402/pay round trip."""
    session = FakeSession([FakeResponse(200, {"article_id": "a/b"})])
    client = PxkeClient(session=session)

    result = client.read_article("a/b slug")

    assert result == {"article_id": "a/b"}
    assert len(session.calls) == 1
    assert session.calls[0].url == f"{BASE_URL}/api/v1/x402/news/articles/a%2Fb%20slug"
