"""CORS origin-allow decisions across permissive/dev/prod configurations."""

from __future__ import annotations

import pytest

from app.core import cors
from app.core.cors import (
    DEFAULT_CORS_EXPOSE_HEADERS,
    DEFAULT_CORS_HEADERS,
    cors_permissive,
    origin_allowed,
)


def test_origin_allowed_only_for_configured_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allows an origin on the configured allowlist and rejects one that isn't."""
    monkeypatch.setattr(cors.settings, "cors_permissive", False)  # isolate allowlist logic
    allowed = ["https://algorand.pxke.me", "https://admin.pxke.me"]
    assert origin_allowed("https://admin.pxke.me", allowed)
    assert not origin_allowed("https://evil.example", allowed)


def test_origin_allowed_permissive_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allows any origin when cors_permissive is unset and app_env is dev."""
    monkeypatch.setattr(cors.settings, "cors_permissive", None)
    monkeypatch.setattr(cors.settings, "app_env", "dev")
    assert origin_allowed("https://anything.example", ["https://algorand.pxke.me"])


def test_prod_is_not_permissive_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejects a non-allowlisted origin in prod, with no wildcard or reflect-anything fallback."""
    # The security-critical invariant: in prod, an origin not on the allowlist is
    # rejected — no wildcard, no reflect-anything fallback.
    monkeypatch.setattr(cors.settings, "cors_permissive", None)
    monkeypatch.setattr(cors.settings, "app_env", "prod")
    assert not cors_permissive()
    assert not origin_allowed("https://evil.example", ["https://algorand.pxke.me"])


def test_explicit_permissive_flag_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit cors_permissive=False wins even in dev app_env."""
    monkeypatch.setattr(cors.settings, "cors_permissive", False)
    monkeypatch.setattr(cors.settings, "app_env", "dev")
    assert not cors_permissive()


def test_payment_headers_are_on_the_cors_allow_and_expose_lists() -> None:
    """Browser x402 clients send PAYMENT-SIGNATURE and must read PAYMENT-REQUIRED."""
    assert "PAYMENT-SIGNATURE" in DEFAULT_CORS_HEADERS
    assert "PAYMENT-REQUIRED" in DEFAULT_CORS_EXPOSE_HEADERS
