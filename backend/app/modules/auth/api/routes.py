"""HTTP routes for wallet authentication."""

from __future__ import annotations

from robyn import Request, Response, Robyn

from app.core import serialization
from app.core.errors import PlatformError
from app.core.http_errors import json_error_from_platform, json_error_response
from app.modules.auth.models.schemas import NonceRequest, VerifyRequest
from app.modules.auth.services.auth_service import AuthService
from app.modules.auth.services.session_store import SessionStore


def register_auth_routes(app: Robyn) -> None:
    """Register the nonce and verify endpoints for wallet authentication."""
    auth_service = AuthService(session_store=SessionStore())

    @app.post("/api/v1/auth/nonce")
    async def auth_nonce(request: Request) -> Response:
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

    @app.post("/api/v1/auth/verify-wallet-signature")
    async def auth_verify(request: Request) -> Response:
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
        return {
            "session_token": token,
            "wallet_address": session_info.wallet_address,
            "issued_at_epoch": session_info.issued_at_epoch,
            "expires_in_epoch": session_info.expires_in_epoch,
            "expires_in_seconds": auth_service.session_ttl,
            "proof_method": proof_method,
        }

    @app.get("/api/v1/auth/session")
    async def auth_session(request: Request) -> Response:
        token = request.headers.get("x-session-token") or ""
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

    @app.post("/api/v1/auth/logout")
    async def auth_logout(request: Request) -> dict[str, bool]:
        token = request.headers.get("x-session-token") or ""
        if token:
            auth_service.revoke_session(token)
        return {"ok": True}
