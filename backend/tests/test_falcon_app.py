"""Falcon app smoke tests: health, CORS preflight, API robots header."""

from __future__ import annotations

import pytest
from falcon import testing

from app.core import cors
from app.falcon_main import create_app


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


def test_cors_rejects_disallowed_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cors.settings, "cors_allowed_origins", "https://algorand.pxke.me")
    monkeypatch.setattr(cors.settings, "cors_permissive", False)
    monkeypatch.setattr(cors.settings, "app_env", "prod")
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
