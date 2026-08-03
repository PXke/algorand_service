"""package_download_stats writer tool (2026-08-03): a tool-gap suggestion from a real compose ("AlgoKit npm/pypi download statistics ... verifiable, third-party adoption metrics") — free, unauthenticated npm/PyPI registry download counts."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai import research_tools
from app.modules.ai.research_tools import _tool_package_download_stats
from app.modules.ai.research_tools import research_tools as research_tools_fn


def _json_response(url: str, status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


def test_npm_reports_week_and_month_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reports both last-week and last-month download counts for an npm package."""

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        if "last-week" in url:
            return _json_response(url, 200, {"downloads": 1234, "package": "algokit-utils"})
        return _json_response(url, 200, {"downloads": 5678, "package": "algokit-utils"})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_package_download_stats("npm", "algokit-utils")

    assert result["downloads_last_week"] == 1234
    assert result["downloads_last_month"] == 5678
    assert result["source"] == "npmjs.org"
    assert "error" not in result


def test_npm_encodes_scoped_package_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scoped npm package name (@scope/pkg) is URL-encoded in the request path."""
    seen_urls: list[str] = []

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        seen_urls.append(url)
        return _json_response(url, 200, {"downloads": 1})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    _tool_package_download_stats("npm", "@algorandfoundation/algokit-utils")

    assert all("%40algorandfoundation%2Falgokit-utils" in u for u in seen_urls)


def test_npm_not_found_is_not_an_error_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 from npm is reported as a clean not-found, not a raised exception."""
    monkeypatch.setattr(
        research_tools, "_guarded_get", lambda url, **_kw: _json_response(url, 404, {})
    )
    result = _tool_package_download_stats("npm", "definitely-not-a-real-package-xyz")
    assert result["error"] == "package not found on npm"


def test_pypi_reports_day_week_and_month_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reports last_day/last_week/last_month from pypistats.org's recent endpoint."""
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **_kw: _json_response(
            url,
            200,
            {"data": {"last_day": 10, "last_week": 100, "last_month": 400}, "package": "algokit"},
        ),
    )
    result = _tool_package_download_stats("pypi", "algokit")

    assert result["downloads_last_day"] == 10
    assert result["downloads_last_week"] == 100
    assert result["downloads_last_month"] == 400
    assert result["source"] == "pypistats.org"


def test_pypi_not_found_is_not_an_error_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 from pypistats is reported as a clean not-found, not a raised exception."""
    monkeypatch.setattr(
        research_tools, "_guarded_get", lambda url, **_kw: _json_response(url, 404, {})
    )
    result = _tool_package_download_stats("pypi", "definitely-not-a-real-package-xyz")
    assert result["error"] == "package not found on PyPI"


def test_requires_nonempty_package() -> None:
    """Rejects an empty package name with an error, no network call."""
    result = _tool_package_download_stats("npm", "")
    assert "error" in result


def test_rejects_unknown_registry() -> None:
    """Rejects a registry other than npm/pypi with an error, no network call."""
    result = _tool_package_download_stats("cargo", "some-crate")
    assert "error" in result


def test_network_error_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network exception is caught and returned as an error, never raised."""

    def raise_error(_url: str, **_kw: object) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(research_tools, "_guarded_get", raise_error)
    result = _tool_package_download_stats("npm", "algokit-utils")
    assert "error" in result


def test_tool_registered() -> None:
    """package_download_stats is registered as a tool schema and handler."""
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "package_download_stats" in names
    assert "package_download_stats" in handlers


def test_schema_requires_registry_and_package() -> None:
    """Declares both registry and package as required parameters."""
    schemas, _handlers = research_tools_fn()
    schema = next(s for s in schemas if s["function"]["name"] == "package_download_stats")
    assert set(schema["function"]["parameters"]["required"]) == {"registry", "package"}
