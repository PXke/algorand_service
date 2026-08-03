"""search_nfd_directory and app_store_metrics writer tools (2026-08-03): tool-gap suggestions from real composes -- resolving an NFD .algo name's on-chain owner (AlgoGazer), and verifiable app-store adoption numbers for a mobile wallet."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai import research_tools
from app.modules.ai.research_tools import _tool_app_store_metrics, _tool_search_nfd_directory
from app.modules.ai.research_tools import research_tools as research_tools_fn


def _json_response(url: str, status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


def test_nfd_forward_lookup_resolves_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """A .algo name resolves to its owner address."""
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **_kw: _json_response(
            url,
            200,
            {
                "name": "gazer.algo",
                "owner": "GAZERVCVUGVKKRBXU7PKIUHXG6HPFOEHU6E4ARPUHZAR3VBBANASPXLE4Y",
                "depositAccount": "GAZERVCVUGVKKRBXU7PKIUHXG6HPFOEHU6E4ARPUHZAR3VBBANASPXLE4Y",
                "properties": {"userDefined": {"url": "https://algogazer.app"}},
            },
        ),
    )
    result = _tool_search_nfd_directory(name="gazer.algo")
    assert result["found"] is True
    assert result["owner"] == "GAZERVCVUGVKKRBXU7PKIUHXG6HPFOEHU6E4ARPUHZAR3VBBANASPXLE4Y"
    assert result["url"] == "https://algogazer.app"


def test_nfd_forward_lookup_appends_algo_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare name without the .algo suffix is normalized before the request."""
    seen: list[str] = []

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        seen.append(url)
        return _json_response(url, 200, {"name": "gazer.algo", "owner": "X"})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    _tool_search_nfd_directory(name="gazer")

    assert any(u.endswith("/nfd/gazer.algo") for u in seen)


def test_nfd_forward_lookup_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 from the NFD API is reported as not-found, not an error."""
    monkeypatch.setattr(
        research_tools, "_guarded_get", lambda url, **_kw: _json_response(url, 404, {})
    )
    result = _tool_search_nfd_directory(name="doesnotexist12345")
    assert result["found"] is False


def test_nfd_reverse_lookup_resolves_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """An address reverse-resolves to the NFD name it owns."""
    addr = "GAZERVCVUGVKKRBXU7PKIUHXG6HPFOEHU6E4ARPUHZAR3VBBANASPXLE4Y"
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **_kw: _json_response(
            url, 200, {addr: {"name": "gazer.algo", "state": "owned", "expired": False}}
        ),
    )
    result = _tool_search_nfd_directory(address=addr)
    assert result["found"] is True
    assert result["name"] == "gazer.algo"
    assert result["state"] == "owned"


def test_nfd_reverse_lookup_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """An address with no NFD is reported as not-found, not an error."""
    addr = "SOMEADDRESS"
    monkeypatch.setattr(
        research_tools, "_guarded_get", lambda url, **_kw: _json_response(url, 200, {})
    )
    result = _tool_search_nfd_directory(address=addr)
    assert result["found"] is False


def test_nfd_requires_name_or_address() -> None:
    """Rejects a call with neither name nor address, no network call."""
    result = _tool_search_nfd_directory()
    assert "error" in result


def test_nfd_network_error_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network exception is caught and returned as an error, never raised."""

    def raise_error(_url: str, **_kw: object) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(research_tools, "_guarded_get", raise_error)
    result = _tool_search_nfd_directory(name="gazer.algo")
    assert "error" in result


def test_nfd_tool_registered() -> None:
    """search_nfd_directory is registered as a tool schema and handler."""
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "search_nfd_directory" in names
    assert "search_nfd_directory" in handlers


def test_app_store_metrics_reports_rating_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reports rating count and average rating for matching iOS apps."""
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **_kw: _json_response(
            url,
            200,
            {
                "results": [
                    {
                        "trackName": "Pera Algo Wallet",
                        "bundleId": "com.algorandllc.algorand",
                        "sellerName": "Algorand LLC",
                        "userRatingCount": 7954,
                        "averageUserRating": 4.77,
                        "userRatingCountForCurrentVersion": 120,
                    }
                ]
            },
        ),
    )
    result = _tool_app_store_metrics("Pera Wallet Algorand")
    assert result["platform"] == "ios"
    assert result["count"] == 1
    assert result["results"][0]["rating_count"] == 7954
    assert result["results"][0]["app_name"] == "Pera Algo Wallet"


def test_app_store_metrics_no_results_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero-result search returns an empty results list, not an error."""
    monkeypatch.setattr(
        research_tools, "_guarded_get", lambda url, **_kw: _json_response(url, 200, {"results": []})
    )
    result = _tool_app_store_metrics("some nonexistent app xyz")
    assert result["count"] == 0
    assert result["results"] == []
    assert "error" not in result


def test_app_store_metrics_requires_nonempty_term() -> None:
    """Rejects an empty search term with an error, no network call."""
    result = _tool_app_store_metrics("")
    assert "error" in result


def test_app_store_metrics_network_error_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network exception is caught and returned as an error, never raised."""

    def raise_error(_url: str, **_kw: object) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(research_tools, "_guarded_get", raise_error)
    result = _tool_app_store_metrics("Pera Wallet")
    assert "error" in result


def test_app_store_metrics_tool_registered() -> None:
    """app_store_metrics is registered as a tool schema and handler."""
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "app_store_metrics" in names
    assert "app_store_metrics" in handlers
