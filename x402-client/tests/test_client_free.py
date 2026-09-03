"""Free methods: right URL, right params, no payment machinery touched at all."""

from __future__ import annotations

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


def test_search_passes_tag_category_limit_and_drops_unset_ones() -> None:
    session = FakeSession([FakeResponse(200, {"items": []})])
    client = PxkeClient(session=session)

    client.search(tag="fx", limit=10)

    call = session.calls[0]
    assert call.url == f"{BASE_URL}/api/v1/x402/search"
    assert call.params == {"tag": "fx", "limit": 10}  # category omitted, not "category": None


def test_probe_sends_url_as_a_query_param() -> None:
    session = FakeSession([FakeResponse(200, {"url": "https://example.com"})])
    client = PxkeClient(session=session)

    client.probe("https://example.com")

    assert session.calls[0].url == f"{BASE_URL}/api/v1/x402/directory/probe"
    assert session.calls[0].params == {"url": "https://example.com"}


def test_probe_history_sends_url_and_limit_and_needs_no_mnemonic() -> None:
    session = FakeSession([FakeResponse(200, {"url": "https://example.com", "history": []})])
    client = PxkeClient(session=session)

    client.probe_history("https://example.com", limit=10)

    assert session.calls[0].url == f"{BASE_URL}/api/v1/x402/directory/probe/history"
    assert session.calls[0].params == {"url": "https://example.com", "limit": 10}


def test_file_feature_request_is_free_and_needs_no_mnemonic() -> None:
    session = FakeSession([FakeResponse(201, {"request": {"request_id": "abc"}})])
    client = PxkeClient(session=session)  # no mnemonic

    result = client.file_feature_request("Add EURQ pricing", "Please add EURQ to /list")

    assert result == {"request": {"request_id": "abc"}}
    call = session.calls[0]
    assert call.method == "POST"
    assert call.url == f"{BASE_URL}/api/v1/x402/features"
    assert call.json_body == {
        "title": "Add EURQ pricing",
        "description": "Please add EURQ to /list",
    }


def test_a_4xx_on_a_free_route_raises_pxke_http_error_with_the_server_message() -> None:
    session = FakeSession(
        [FakeResponse(400, {"error": {"code": "bad_request", "message": "tag and category together"}})]
    )
    client = PxkeClient(session=session)

    try:
        client.search(tag="fx", category="data")
    except PxkeHTTPError as exc:
        assert exc.status_code == 400
        assert "tag and category together" in str(exc)
        assert exc.body == {"error": {"code": "bad_request", "message": "tag and category together"}}
    else:
        raise AssertionError("expected PxkeHTTPError")


def test_all_read_only_free_methods_hit_their_documented_routes() -> None:
    # One call each: board / features / grades / news / settlements_recent.
    routes_and_calls = [
        (lambda c: c.board(limit=5), "GET", "/api/v1/x402/board"),
        (lambda c: c.features(), "GET", "/api/v1/x402/features"),
        (lambda c: c.grades(), "GET", "/api/v1/x402/grades"),
        (lambda c: c.news(tag="algorand"), "GET", "/api/v1/x402/news"),
        (lambda c: c.settlements_recent(), "GET", "/api/v1/x402/settlements/recent"),
    ]
    for make_call, method, path in routes_and_calls:
        session = FakeSession([FakeResponse(200, {"items": []})])
        client = PxkeClient(session=session)

        make_call(client)

        assert session.calls[0].method == method
        assert session.calls[0].url == f"{BASE_URL}{path}"


def test_read_article_is_free_and_url_encodes_the_article_id() -> None:
    """The article route carries no payment gate: a plain 200, no 402/pay round trip."""
    session = FakeSession([FakeResponse(200, {"article_id": "a/b"})])
    client = PxkeClient(session=session)

    result = client.read_article("a/b slug")

    assert result == {"article_id": "a/b"}
    assert len(session.calls) == 1
    assert session.calls[0].url == f"{BASE_URL}/api/v1/x402/news/articles/a%2Fb%20slug"


def test_social_agent_feed_and_agent_are_free() -> None:
    session = FakeSession([FakeResponse(200, {"posts": []})])
    client = PxkeClient(session=session)

    client.social_agent_feed("AGENT1", limit=5)

    assert session.calls[0].url == f"{BASE_URL}/api/v1/x402/social/agents/AGENT1/feed"
    assert session.calls[0].params == {"limit": 5}

    session2 = FakeSession([FakeResponse(200, {"wallet": "AGENT1"})])
    client2 = PxkeClient(session=session2)

    client2.social_agent("AGENT1")

    assert session2.calls[0].url == f"{BASE_URL}/api/v1/x402/social/agents/AGENT1"
