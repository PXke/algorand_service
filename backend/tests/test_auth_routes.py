"""auth_verify/auth_logout's cross-subdomain wallet_session cookie (2026-09-07).

The admin panel disappeared when moving between algorand.pxke.me/
x402.pxke.me/algorand-registry.pxke.me because the session token lived only
in localStorage, which is per-origin. auth_verify now also mints a
Domain-scoped `wallet_session` cookie (only when settings.session_cookie_domain
is configured), and auth_logout clears it. `auth_service` is monkeypatched
directly (a simple stand-in with the two methods these routes call) rather
than wiring a real Redis-backed SessionStore -- these tests are about the
cookie plumbing, not session storage itself (covered by test_session_store.py).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.http import QueryParams, Request
from app.modules.auth.api import routes as auth_routes
from app.schemas import SessionInfo

_WALLET = "W" * 58


def _request(*, headers: dict[str, str] | None = None, body: bytes = b"{}") -> Request:
    return Request(
        method="POST",
        headers=headers or {},
        query_params=QueryParams({}),
        path_params={},
        body=body,
        url=SimpleNamespace(
            scheme="https", host="algorand-api.pxke.me", path="/api/v1/auth/verify-wallet-signature"
        ),
    )


class _FakeAuthService:
    """Stands in for AuthService -- only the two methods these routes call."""

    def __init__(self, *, verified: tuple[str, SessionInfo, str] | None) -> None:
        self._verified = verified
        self.session_ttl = 3600
        self.revoked: list[str] = []

    def verify_nonce_signature(self, **_kw: object) -> tuple[str, SessionInfo, str] | None:
        return self._verified

    def revoke_session(self, token: str) -> None:
        self.revoked.append(token)


def _verify_body() -> bytes:
    return json.dumps(
        {
            "wallet_address": _WALLET,
            "nonce": "n",
            "proof_method": "signed_bytes",
            "signature_b64": "AA==",
        }
    ).encode()


def test_verify_sets_no_cookie_when_domain_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local/dev default (session_cookie_domain=""): plain dict body, no Set-Cookie -- unchanged behavior."""
    monkeypatch.setattr(settings, "session_cookie_domain", "")
    session_info = SessionInfo(wallet_address=_WALLET, issued_at_epoch=1000, expires_in_epoch=4600)
    monkeypatch.setattr(
        auth_routes,
        "auth_service",
        _FakeAuthService(verified=("TOK1", session_info, "signed_bytes")),
    )

    result = auth_routes.auth_verify(_request(body=_verify_body()))

    assert isinstance(result, dict)
    assert result["session_token"] == "TOK1"


def test_verify_sets_the_cross_subdomain_cookie_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """session_cookie_domain=".pxke.me": the response carries a Domain-scoped, HttpOnly, Secure Set-Cookie alongside the same JSON body."""
    monkeypatch.setattr(settings, "session_cookie_domain", ".pxke.me")
    session_info = SessionInfo(wallet_address=_WALLET, issued_at_epoch=1000, expires_in_epoch=4600)
    monkeypatch.setattr(
        auth_routes,
        "auth_service",
        _FakeAuthService(verified=("TOK1", session_info, "signed_bytes")),
    )

    response = auth_routes.auth_verify(_request(body=_verify_body()))

    cookie = response.headers["Set-Cookie"]
    assert "wallet_session=TOK1" in cookie
    assert "Domain=.pxke.me" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=Lax" in cookie
    assert "Max-Age=3600" in cookie
    body = json.loads(response.description)
    assert body["session_token"] == "TOK1"
    assert body["wallet_address"] == _WALLET


def test_verify_failure_never_sets_a_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed signature verification is a plain 401, never a Set-Cookie -- nothing to authenticate."""
    monkeypatch.setattr(settings, "session_cookie_domain", ".pxke.me")
    monkeypatch.setattr(auth_routes, "auth_service", _FakeAuthService(verified=None))

    response = auth_routes.auth_verify(_request(body=_verify_body()))

    assert response.status_code == 401
    assert "Set-Cookie" not in response.headers


def test_logout_clears_the_cookie_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: a cookie-only session (no x-session-token header) must still be revoked and the cookie cleared -- session_token() falls back to reading the Cookie header itself."""
    monkeypatch.setattr(settings, "session_cookie_domain", ".pxke.me")
    fake = _FakeAuthService(verified=None)
    monkeypatch.setattr(auth_routes, "auth_service", fake)

    response = auth_routes.auth_logout(_request(headers={"Cookie": "wallet_session=TOK1"}))

    assert fake.revoked == ["TOK1"]
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith("wallet_session=;")
    assert "Domain=.pxke.me" in cookie
    assert "Max-Age=0" in cookie
    body = json.loads(response.description)
    assert body == {"ok": True}


def test_logout_sets_no_cookie_when_domain_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local/dev default: logout still revokes, just no Set-Cookie -- unchanged behavior."""
    monkeypatch.setattr(settings, "session_cookie_domain", "")
    fake = _FakeAuthService(verified=None)
    monkeypatch.setattr(auth_routes, "auth_service", fake)

    result = auth_routes.auth_logout(_request(headers={"x-session-token": "TOK1"}))

    assert fake.revoked == ["TOK1"]
    assert result == {"ok": True}


def test_logout_with_no_token_at_all_still_clears_the_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to revoke server-side, but the cookie is cleared regardless -- a stale/already-expired cookie should not linger in the browser."""
    monkeypatch.setattr(settings, "session_cookie_domain", ".pxke.me")
    fake = _FakeAuthService(verified=None)
    monkeypatch.setattr(auth_routes, "auth_service", fake)

    response = auth_routes.auth_logout(_request(headers={}))

    assert fake.revoked == []
    assert "Set-Cookie" in response.headers
