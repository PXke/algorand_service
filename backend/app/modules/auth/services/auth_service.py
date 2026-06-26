from __future__ import annotations

import json
import secrets

from app.core.config import settings
from app.core.errors import PlatformError
from app.modules.auth.models.schemas import Arc0060Proof, Caip122Payload, SessionInfo
from app.modules.auth.services.session_store import SessionStore
from app.modules.auth.utils.algorand_txn_verify import verify_auth_transaction
from app.modules.auth.utils.algorand_verify import verify_wallet_signature
from app.modules.auth.utils.arc0060_verify import verify_arc0060_auth
from app.modules.auth.utils.caip122 import Caip122Message
from app.modules.auth.utils.signing_message import AuthChallenge, build_auth_challenge


class AuthService:
    def __init__(self, session_store: SessionStore) -> None:
        self._store = session_store

    def issue_nonce(self, wallet_address: str) -> AuthChallenge:
        if not self._store.allow_nonce_issue(wallet_address):
            raise PlatformError(
                "rate_limited",
                "Too many nonce requests; try again in a minute",
                http_status=429,
            )
        nonce = secrets.token_urlsafe(24)
        challenge = build_auth_challenge(nonce, wallet_address)
        self._store.set_nonce_challenge(
            wallet_address,
            json.dumps(
                {
                    "nonce": challenge.nonce,
                    "signing_message": challenge.signing_message,
                    "caip122": challenge.caip122.to_dict(),
                }
            ),
        )
        return challenge

    def verify_nonce_signature(
        self,
        wallet_address: str,
        nonce: str,
        *,
        proof_method: str = "arc0060",
        signature_b64: str | None = None,
        signed_txn_b64: str | None = None,
        arc0060: Arc0060Proof | None = None,
    ) -> tuple[str, SessionInfo, str] | None:
        raw = self._store.pop_nonce_challenge(wallet_address)
        if not raw:
            return None

        try:
            stored = json.loads(raw)
        except json.JSONDecodeError:
            return None

        if stored.get("nonce") != nonce:
            return None

        signing_message = str(stored["signing_message"])
        expected_caip122 = Caip122Message.from_dict(stored["caip122"])

        verified = False
        if proof_method == "arc0060" and arc0060 is not None:
            verified = verify_arc0060_auth(
                wallet_address,
                data_b64=arc0060.data_b64,
                signature_b64=arc0060.signature_b64,
                authenticator_data_b64=arc0060.authenticator_data_b64,
                domain=arc0060.domain,
                expected_caip122=expected_caip122,
            )
        elif proof_method == "arc0025_txn" and signed_txn_b64:
            verified = verify_auth_transaction(wallet_address, signing_message, signed_txn_b64)
        elif proof_method == "legacy_message" and signature_b64:
            verified = verify_wallet_signature(wallet_address, signing_message, signature_b64)

        if not verified:
            return None

        token = secrets.token_urlsafe(48)
        rec = self._store.set_session(token=token, wallet_address=wallet_address)
        return (
            token,
            SessionInfo(
                wallet_address=rec.wallet_address,
                issued_at_epoch=rec.issued_at_epoch,
                expires_in_epoch=rec.expires_in_epoch,
            ),
            proof_method,
        )

    def get_session(self, token: str) -> SessionInfo | None:
        rec = self._store.get_session(token)
        if not rec:
            return None
        return SessionInfo(
            wallet_address=rec.wallet_address,
            issued_at_epoch=rec.issued_at_epoch,
            expires_in_epoch=rec.expires_in_epoch,
        )

    def revoke_session(self, token: str) -> None:
        self._store.delete_session(token)

    def caip122_payload(self, challenge: AuthChallenge) -> Caip122Payload:
        return Caip122Payload.model_validate(challenge.caip122.to_dict())

    @property
    def session_ttl(self) -> int:
        return settings.session_ttl_seconds

    @property
    def nonce_ttl(self) -> int:
        return settings.nonce_ttl_seconds
