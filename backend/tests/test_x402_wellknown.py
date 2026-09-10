"""Tests for the marketplace discovery bootstrap: /.well-known/x402 and /openapi.json.

Fully offline: Redis is a fake at the get_redis seam (shared with the
catalog's own rate limiter, since these routes reuse it) and nothing here
settles a payment.
"""

from __future__ import annotations

import re
from typing import Never

import pytest

pytest.importorskip("x402")

from falcon import testing
from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request
from app.falcon_main import create_app
from app.modules.x402_catalog.services import catalog as catalog_service
from app.modules.x402_wellknown.api import routes as wellknown_routes
from app.modules.x402_wellknown.services import openapi_spec

_PAY_TO = "A" * 58


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        _ = key, seconds
        return True


class _BrokenRedis:
    def incr(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def expire(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")


def _request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        method="GET",
        headers=headers or {},
        query_params=QueryParams({}),
        path_params={},
        body=b"",
        url=None,
    )


def _configure(monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True, **stores: str) -> None:
    """x402 on, every product store durable by default so paid+free routes both appear."""
    monkeypatch.setattr(settings, "x402_enabled", enabled)
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", _PAY_TO)
    monkeypatch.setattr(settings, "x402_public_api_base", "https://algorand-api.pxke.me")
    monkeypatch.setattr(settings, "x402_scan_enabled", True)
    monkeypatch.setattr(settings, "x402_storage_local_root", "/tmp/x402-storage-wellknown-test")
    for setting in ("news_store", "x402_storage_meta_store"):
        monkeypatch.setattr(settings, setting, stores.get(setting, "cassandra"))
    monkeypatch.setattr(rate_limit_core, "get_redis", _FakeRedis)


# --------------------------------------------------------------------------- #
# /.well-known/x402
# --------------------------------------------------------------------------- #
def test_wellknown_route_is_registered_only_when_x402_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/.well-known/x402 is a meta route, gated only on the shared x402_enabled switch."""
    _configure(monkeypatch, enabled=False)
    assert testing.TestClient(create_app()).simulate_get("/.well-known/x402").status_code == 404

    _configure(monkeypatch, enabled=True)
    response = testing.TestClient(create_app()).simulate_get("/.well-known/x402")
    assert response.status_code == 200
    assert response.json["pay_to"] == _PAY_TO


def test_wellknown_content_matches_the_catalog_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """/.well-known/x402 re-serves build_catalog() verbatim -- no real spec to diverge from."""
    _configure(monkeypatch)
    result = wellknown_routes.x402_wellknown(_request())
    assert isinstance(result, dict)
    assert result == catalog_service.build_catalog()


def test_wellknown_is_rate_limited_and_shares_the_catalog_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Well-known reuses catalog_rate_limited's counter, fails open on a Redis error."""
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "x402_catalog_rate_limit_per_hour", 2)
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda: fake)
    headers = {"x-real-ip": "203.0.113.20"}

    # One hit against the catalog route itself, then one against well-known:
    # they must share the same per-IP counter, so the THIRD call anywhere
    # (regardless of which of the two paths) is rate limited.
    from app.modules.x402_catalog.api import routes as catalog_routes

    assert isinstance(catalog_routes.x402_catalog(_request(headers)), dict)
    assert isinstance(wellknown_routes.x402_wellknown(_request(headers)), dict)
    limited = wellknown_routes.x402_wellknown(_request(headers))
    assert limited.status_code == 429

    # Fails open on a Redis failure.
    monkeypatch.setattr(rate_limit_core, "get_redis", _BrokenRedis)
    assert isinstance(wellknown_routes.x402_wellknown(_request(headers)), dict)


# --------------------------------------------------------------------------- #
# /openapi.json
# --------------------------------------------------------------------------- #
def test_openapi_route_is_registered_only_when_x402_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/openapi.json is a meta route, gated only on the shared x402_enabled switch."""
    _configure(monkeypatch, enabled=False)
    assert testing.TestClient(create_app()).simulate_get("/openapi.json").status_code == 404

    _configure(monkeypatch, enabled=True)
    response = testing.TestClient(create_app()).simulate_get("/openapi.json")
    assert response.status_code == 200
    body = response.json
    assert {"openapi", "info", "paths"}.issubset(body.keys())
    assert body["openapi"].startswith("3.1")


def test_openapi_is_valid_shaped_and_lists_a_paid_and_a_free_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """openapi.json is a well-formed OpenAPI 3.1 document with real paid/free route entries."""
    _configure(monkeypatch)
    spec = openapi_spec.build_openapi()
    assert spec["openapi"] == "3.1.0"
    assert "title" in spec["info"]
    assert "description" in spec["info"]
    assert isinstance(spec["paths"], dict)
    assert spec["paths"]

    # A known paid route: POST /api/v1/x402/scan/url.
    paid_op = spec["paths"]["/api/v1/x402/scan/url"]["post"]
    assert paid_op["x-x402-paid"] is True
    assert paid_op["x-x402-resource"] == "x402-scan-url"
    assert paid_op["x-x402-price-usd"] == settings.x402_scan_price
    assert paid_op["x-x402-supports-preview"] is True
    assert "x-x402-supports-receipts" not in paid_op
    assert paid_op["responses"]["402"]

    # A known free route: GET /api/v1/x402/news.
    free_op = spec["paths"]["/api/v1/x402/news"]["get"]
    assert free_op["x-x402-paid"] is False
    assert "402" not in free_op["responses"]

    # A path-param route is rewritten to OpenAPI's {param} form.
    assert "/api/v1/x402/news/articles/{article_id}" in spec["paths"]
    article_op = spec["paths"]["/api/v1/x402/news/articles/{article_id}"]["get"]
    assert {p["name"] for p in article_op["parameters"]} == {"article_id"}

    # The two bootstrap routes describe themselves.
    assert spec["paths"]["/.well-known/x402"]["get"]["x-x402-paid"] is False
    assert spec["paths"]["/openapi.json"]["get"]["x-x402-paid"] is False

    # No fake auth scheme invented; the x402 note lives in info.description instead.
    assert "securitySchemes" not in spec
    assert "security" not in spec
    assert "x402" in spec["info"]["description"].lower()


def test_openapi_paths_are_a_subset_of_registered_routes_when_everything_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing in the generated spec claims a route create_app() did not actually register."""
    _configure(monkeypatch)
    app = create_app()
    spec = openapi_spec.build_openapi()
    for path, methods in spec["paths"].items():
        if path in (openapi_spec.WELLKNOWN_PATH, openapi_spec.OPENAPI_PATH):
            continue
        concrete = re.sub(r"\{(\w+)\}", "x", path)
        found = app._router.find(concrete)
        assert found is not None, path
        resource = found[0]
        registered_methods = {m.lower() for m in getattr(resource, "_methods", {})}
        assert set(methods.keys()).issubset(registered_methods), path
