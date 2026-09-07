"""HTTP routes for wallet authentication."""

from __future__ import annotations

from app.core import serialization
from app.core.config import settings
from app.core.errors import PlatformError
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.request_headers import SESSION_COOKIE_NAME, session_token
from app.modules.auth.models.schemas import NonceRequest, VerifyRequest
from app.modules.auth.services.auth_service import AuthService
from app.modules.auth.services.session_store import SessionStore

# Redis connection is lazy (redis.from_url doesn't dial until first use), so
# this is safe as a module-level singleton shared by every route.
auth_service = AuthService(session_store=SessionStore())

_COOKIE_ATTRS = "Path=/; Secure; HttpOnly; SameSite=Lax"


def _session_cookie_header(token: str, *, max_age: int) -> str | None:
    """Set-Cookie value for the cross-subdomain session cookie, or None when settings.session_cookie_domain isn't configured (local/dev -- see that setting's own docstring).

    HttpOnly: never exposed to page JS, so an XSS on any of the three
    frontends cannot read it (the frontend never needed to read it either --
    it already treated `x-session-token`/localStorage as the primary path
    and the cookie purely rides along on fetch's `credentials: 'include'`).
    SameSite=Lax is enough (not None): SameSite classification is by
    registrable domain, so a fetch from x402.pxke.me to algorand-api.pxke.me
    is same-site, not cross-site.
    """
    domain = settings.session_cookie_domain.strip()
    if not domain:
        return None
    return f"{SESSION_COOKIE_NAME}={token}; Domain={domain}; Max-Age={max_age}; {_COOKIE_ATTRS}"


def _clear_session_cookie_header() -> str | None:
    """Set-Cookie value that expires the session cookie immediately, or None when settings.session_cookie_domain isn't configured."""
    domain = settings.session_cookie_domain.strip()
    if not domain:
        return None
    return f"{SESSION_COOKIE_NAME}=; Domain={domain}; Max-Age=0; {_COOKIE_ATTRS}"


def auth_nonce(request: Request) -> Response:
    """Issue a fresh login nonce and CAIP-122 challenge for a wallet."""
    try:
        payload = serialization.decode(request.body, NonceRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        challenge = auth_service.issue_nonce(payload.wallet_address)
    except PlatformError as exc:
        return json_error_from_platform(exc)

    caip122 = auth_service.caip122_payload(challenge)
    return {
        "wallet_address": payload.wallet_address,
        "nonce": challenge.nonce,
        "signing_message": challenge.signing_message,
        "caip122": serialization.to_builtins(caip122),
        "expires_in_seconds": auth_service.nonce_ttl,
    }


def auth_verify(request: Request) -> Response:
    """Verify a signed nonce and, on success, mint a new session token."""
    try:
        payload = serialization.decode(request.body, VerifyRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    verified = auth_service.verify_nonce_signature(
        wallet_address=payload.wallet_address,
        nonce=payload.nonce,
        proof_method=payload.proof_method,
        signature_b64=payload.signature_b64,
        signed_txn_b64=payload.signed_txn_b64,
        arc0060=payload.arc0060,
    )
    if verified is None:
        return json_error_response(
            401,
            "invalid_signature_or_nonce",
            "Signature verification failed or nonce expired",
        )

    token, session_info, proof_method = verified
    body = {
        "session_token": token,
        "wallet_address": session_info.wallet_address,
        "issued_at_epoch": session_info.issued_at_epoch,
        "expires_in_epoch": session_info.expires_in_epoch,
        "expires_in_seconds": auth_service.session_ttl,
        "proof_method": proof_method,
    }
    cookie = _session_cookie_header(token, max_age=auth_service.session_ttl)
    if cookie is None:
        return body
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", "Set-Cookie": cookie},
        description=serialization.dumps(body),
    )


def auth_session(request: Request) -> Response:
    """Look up the active session for the given session token."""
    token = session_token(request.headers)
    if not token:
        return json_error_response(401, "missing_session_token", "Session token required")

    info = auth_service.get_session(token)
    if info is None:
        return json_error_response(
            401,
            "invalid_or_expired_session",
            "Session is invalid or expired",
        )
    return serialization.to_builtins(info)


def auth_logout(request: Request) -> Response | dict[str, bool]:
    """Revoke the session behind the given session token (header or cookie), if any, and clear the cross-subdomain cookie."""
    token = session_token(request.headers)
    if token:
        auth_service.revoke_session(token)
    cookie = _clear_session_cookie_header()
    if cookie is None:
        return {"ok": True}
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", "Set-Cookie": cookie},
        description=serialization.dumps({"ok": True}),
    )


def register_auth_routes(app: Router) -> None:
    """Register the nonce and verify endpoints for wallet authentication."""
    app.post("/api/v1/auth/nonce")(auth_nonce)
    app.post("/api/v1/auth/verify-wallet-signature")(auth_verify)
    app.get("/api/v1/auth/session")(auth_session)
    app.post("/api/v1/auth/logout")(auth_logout)
