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

    Each product is gated on its own store setting so a product left on
    "memory" (the dev/test-only default, CLAUDE.md section 9) never accepts a
    real settled payment it cannot reliably honor across gunicorn's worker
    processes. Every kept store defaults to "memory" here, so only the
    gate-free catalog and bootstrap routes exist -- exercised via the free
    GET each product exposes, so this never touches a real store.
    """
    monkeypatch.setattr(falcon_main_settings, "x402_enabled", True)
    client = testing.TestClient(create_app())
    for path in (
        "/api/v1/x402/news",
        "/api/v1/x402/storage/backups",
        "/api/v1/x402/scan/url",
    ):
        resp = client.simulate_get(path)
        assert resp.status_code == 404, f"{path} should not be registered on the memory store"
    assert client.simulate_get("/api/v1/x402").status_code == 200
    assert client.simulate_get("/api/v1/x402/settlements").status_code == 200
    assert client.simulate_get("/.well-known/x402").status_code == 200


def test_a_paid_x402_product_off_the_memory_store_is_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mirror of the test above: a non-memory store setting registers that product's routes."""
    monkeypatch.setattr(falcon_main_settings, "x402_enabled", True)
    monkeypatch.setattr(falcon_main_settings, "news_store", "cassandra")
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/api/v1/x402/news")
    assert resp.status_code != 404


def test_removed_x402_products_are_never_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner decision 2026-09-10: directory, board, features, grades, social, uptime, KYA, receipts and ping are gone -- every one of their paths 404s even with x402 on."""
    monkeypatch.setattr(falcon_main_settings, "x402_enabled", True)
    monkeypatch.setattr(falcon_main_settings, "news_store", "cassandra")
    client = testing.TestClient(create_app())
    for path in (
        "/api/v1/x402/list",
        "/api/v1/x402/search",
        "/api/v1/x402/board",
        "/api/v1/x402/features",
        "/api/v1/x402/grades",
        "/api/v1/x402/social/agents",
        "/api/v1/x402/uptime/check",
        "/api/v1/kyc/verify",
        "/api/v1/x402/receipts/abc",
        "/api/v1/x402/ping",
    ):
        assert client.simulate_get(path).status_code == 404, path
        assert client.simulate_post(path).status_code == 404, path


def test_paid_routes_refuse_to_start_outside_dev_on_a_memory_settlement_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLAUDE.md section 9: every settlement is logged, durably. A non-dev process with x402 on and the ledger on "memory" must fail at startup, not serve paid routes against a per-process dict."""
    monkeypatch.setattr(falcon_main_settings, "x402_enabled", True)
    monkeypatch.setattr(falcon_main_settings, "app_env", "prod")
    monkeypatch.setattr(falcon_main_settings, "x402_settlement_store", "memory")
    with pytest.raises(RuntimeError, match="X402_SETTLEMENT_STORE"):
        create_app()

    # Durable ledger: boots. Dev on memory: boots (the memory backend is for dev/test).
    monkeypatch.setattr(falcon_main_settings, "x402_settlement_store", "cassandra")
    assert testing.TestClient(create_app()).simulate_get("/api/v1/x402").status_code == 200
    monkeypatch.setattr(falcon_main_settings, "app_env", "dev")
    monkeypatch.setattr(falcon_main_settings, "x402_settlement_store", "memory")
    assert testing.TestClient(create_app()).simulate_get("/api/v1/x402").status_code == 200

    # x402 off: the guard is not consulted at all.
    monkeypatch.setattr(falcon_main_settings, "x402_enabled", False)
    monkeypatch.setattr(falcon_main_settings, "app_env", "prod")
    assert testing.TestClient(create_app()).simulate_get("/api/v1/x402").status_code == 404


def test_ecosystem_left_on_the_memory_store_is_not_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same gate as the paid x402 products above, applied to the free registry (2026-09-08 Fable review: this gate did not exist at all before -- ecosystem_enabled alone registered the routes regardless of durability, so an unset/regressed ECOSYSTEM_STORE would silently serve the Algorand Open Registry from a per-process dict that a gunicorn restart erases, with no error anywhere)."""
    monkeypatch.setattr(falcon_main_settings, "ecosystem_enabled", True)
    monkeypatch.setattr(falcon_main_settings, "ecosystem_store", "memory")
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/api/v1/ecosystem")
    assert resp.status_code == 404


def test_ecosystem_off_the_memory_store_is_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror of the test above: a non-memory store setting registers the registry's routes."""
    monkeypatch.setattr(falcon_main_settings, "ecosystem_enabled", True)
    monkeypatch.setattr(falcon_main_settings, "ecosystem_store", "cassandra")
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/api/v1/ecosystem")
    assert resp.status_code != 404
