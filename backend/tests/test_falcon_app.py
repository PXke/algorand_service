"""Falcon app smoke tests: health, CORS preflight, API robots header."""

from __future__ import annotations

import pytest
from falcon import testing

from app.core import cors
from app.falcon_main import create_app
from app.falcon_main import settings as falcon_main_settings


def test_health() -> None:
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/health")
    assert resp.status_code == 200
    assert resp.json["status"] == "ok"


def test_api_robots_tag() -> None:
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/api/v1/glossary")
    assert resp.headers.get("X-Robots-Tag") == "noindex"


def test_cors_preflight_allowed_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cors.settings, "cors_allowed_origins", "https://algorand.pxke.me")
    monkeypatch.setattr(cors.settings, "cors_permissive", False)
    client = testing.TestClient(create_app())
    resp = client.simulate_options(
        "/health",
        headers={
            "Origin": "https://algorand.pxke.me",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 204
    assert resp.headers.get("Access-Control-Allow-Origin") == "https://algorand.pxke.me"
    allow_headers = resp.headers.get("Access-Control-Allow-Headers") or ""
    assert "PAYMENT-SIGNATURE" in allow_headers


def test_cors_exposes_payment_required_on_allowed_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Browser x402 clients must be able to read PAYMENT-REQUIRED off a 402."""
    monkeypatch.setattr(cors.settings, "cors_allowed_origins", "https://algorand.pxke.me")
    monkeypatch.setattr(cors.settings, "cors_permissive", False)
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/health", headers={"Origin": "https://algorand.pxke.me"})
    expose = resp.headers.get("Access-Control-Expose-Headers") or ""
    assert "PAYMENT-REQUIRED" in expose


def test_health_ready_omits_check_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public /health/ready must not leak exception strings (URLs, passwords)."""
    from app import falcon_main as fm
    from app.core.health import CheckResult

    monkeypatch.setattr(
        fm,
        "run_readiness_checks",
        lambda: [
            CheckResult("redis", False, "Error connecting to redis://:hunter2@10.0.0.5:6379/0")
        ],
    )
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/health/ready")
    assert resp.status_code == 200
    assert resp.json["status"] == "degraded"
    assert resp.json["checks"] == [{"name": "redis", "ok": False}]
    assert "hunter2" not in resp.text
    assert "detail" not in resp.json["checks"][0]


def test_cors_rejects_disallowed_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cors.settings, "cors_allowed_origins", "https://algorand.pxke.me")
    monkeypatch.setattr(cors.settings, "cors_permissive", False)
    monkeypatch.setattr(cors.settings, "app_env", "prod")
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_a_paid_x402_product_left_on_the_memory_store_is_not_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paid route backed by the per-process memory store must not go live.

    x402_enabled alone used to be the only gate; each product is now also
    gated on its own store setting so a product left on "memory" (the
    dev/test-only default, CLAUDE.md section 9) never accepts a real settled
    payment it cannot reliably honor across gunicorn's worker processes. All
    five default to "memory" here, so none of the routes should exist --
    exercised via the free GET each product exposes, so this never touches a
    real store.
    """
    monkeypatch.setattr(falcon_main_settings, "x402_enabled", True)
    client = testing.TestClient(create_app())
    for path in (
        "/api/v1/x402/search",  # directory
        "/api/v1/x402/board",
        "/api/v1/x402/features",
        "/api/v1/x402/grades",
    ):
        resp = client.simulate_get(path)
        assert resp.status_code == 404, f"{path} should not be registered on the memory store"


def test_a_paid_x402_product_off_the_memory_store_is_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mirror of the test above: a non-memory store setting registers that product's routes."""
    monkeypatch.setattr(falcon_main_settings, "x402_enabled", True)
    monkeypatch.setattr(falcon_main_settings, "x402_board_store", "cassandra")
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/api/v1/x402/board")
    assert resp.status_code != 404
