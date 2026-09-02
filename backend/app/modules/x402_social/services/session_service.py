"""Challenge/session issuance and signature verification for x402 social's free-authenticated routes (design doc section 4.2).

Generalizes the KYA single-use consent-challenge pattern
(app/modules/kya/services/consent_challenge.py) -- same Redis single-use
GETDEL consumption (two concurrent logins cannot both redeem one nonce),
same fail-closed-on-a-Redis-error contract for issuing/consuming a
challenge. Signature verification reuses app/modules/auth/utils/ exactly as
AuthService.verify_nonce_signature does (app/modules/auth/services/
auth_service.py) -- same four proof_method verifiers, no new cryptography.

A signature check is the real security boundary for this whole flow (the
design doc's own note); the bearer TOKEN this module also issues is a
convenience for the free-authenticated routes only (PATCH /profile in Phase
S0; more join it in later phases) -- no paid route ever requires one, see
services/profile_service.py's own docstring and design doc section 4.1: the
payment itself is the identity proof there.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from app.core import serialization
from app.core.config import settings
from app.core.redis_client import get_redis
from app.modules.auth.models.schemas import Arc0060Proof
from app.modules.auth.utils.algorand_txn_verify import verify_auth_transaction
from app.modules.auth.utils.algorand_verify import verify_signed_bytes, verify_wallet_signature
from app.modules.auth.utils.arc0060_verify import verify_arc0060_auth
from app.modules.auth.utils.caip122 import Caip122Message, utc_now_iso

_CHALLENGE_KEY_PREFIX = "algorand:x402social:challenge:"
_SESSION_KEY_PREFIX = "algorand:x402social:session:"
# Single-use signing window -- same 300s convention as KYA's own
# kyc_consent_ttl_seconds, not itself a per-product-priced knob so it is not
# a new settings field (CLAUDE.md section 3: config has one owner, but a
# fixed internal window shared with an existing precedent is not a "setting"
# in the sense that rule targets -- it is not read from the environment
# anywhere in this codebase either).
_CHALLENGE_TTL_SECONDS = 300
_SIGNING_STATEMENT = "PXke x402 social session"


class SessionStoreError(Exception):
    """Redis was unreachable while issuing or consuming a challenge/session -- the caller must not proceed on an unconfirmed store."""


@dataclass(frozen=True, slots=True)
class SocialChallenge:
    """The nonce and signing message a wallet must sign to open a free-authenticated session."""

    nonce: str
    signing_message: str
    expires_at: int


def _challenge_key(wallet: str) -> str:
    return f"{_CHALLENGE_KEY_PREFIX}{wallet}"


def _session_key(token: str) -> str:
    return f"{_SESSION_KEY_PREFIX}{token}"


def issue_challenge(wallet: str) -> SocialChallenge:
    """Mint a fresh nonce, persist it until _CHALLENGE_TTL_SECONDS, and return it.

    The signing message embeds a fixed statement, the wallet and the nonce
    (design doc section 4.2: "so a signature can't be replayed cross-
    product"); a full CAIP-122 payload is stored alongside it too, so
    proof_method="arc0060" can be verified against the SAME nonce/domain
    rather than only against its own embedded (unverified-by-us) claims --
    see verify_challenge_signature.

    Raises SessionStoreError when Redis itself fails: the client must not be
    handed a challenge that cannot later be consumed (same contract as
    kya.consent_challenge.issue_consent_challenge).
    """
    nonce = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + _CHALLENGE_TTL_SECONDS
    signing_message = (
        f"{_SIGNING_STATEMENT}\nwallet: {wallet}\nnonce: {nonce}\nexpires_at: {expires_at}"
    )
    caip122 = Caip122Message(
        domain=settings.auth_domain,
        account_address=wallet,
        uri=settings.auth_uri,
        chain_id=settings.auth_caip2_chain_id,
        nonce=nonce,
        statement=_SIGNING_STATEMENT,
        issued_at=utc_now_iso(),
        type="ed25519",
    )
    try:
        get_redis().setex(
            _challenge_key(wallet),
            _CHALLENGE_TTL_SECONDS,
            serialization.dumps(
                {
                    "nonce": nonce,
                    "signing_message": signing_message,
                    "expires_at": expires_at,
                    "caip122": caip122.to_dict(),
                }
            ),
        )
    except Exception as exc:
        raise SessionStoreError("session challenge store unavailable") from exc
    return SocialChallenge(nonce=nonce, signing_message=signing_message, expires_at=expires_at)


def verify_challenge_signature(
    *,
    wallet: str,
    nonce: str,
    proof_method: str,
    signature_b64: str | None = None,
    signed_txn_b64: str | None = None,
    arc0060: Arc0060Proof | None = None,
) -> bool:
    """Consume the pending challenge for `wallet` and verify the signature over it.

    GETDEL (single-use: two concurrent logins cannot both redeem it, same as
    kya.consent_challenge.consume_consent_challenge). Mirrors
    AuthService.verify_nonce_signature's exact proof_method dispatch -- same
    four verifiers, same "any failure is a flat False", no new cryptography.

    Raises SessionStoreError on a Redis failure (fail closed: a session must
    not open on an unconfirmed store).
    """
    try:
        raw = get_redis().getdel(_challenge_key(wallet))
    except Exception as exc:
        raise SessionStoreError("session challenge store unavailable") from exc
    if not raw:
        return False
    try:
        stored = serialization.loads(raw)
    except Exception:
        return False
    if stored.get("nonce") != nonce:
        return False
    if int(stored.get("expires_at", 0)) < int(time.time()):
        return False

    signing_message = str(stored["signing_message"])
    expected_caip122 = Caip122Message.from_dict(stored["caip122"])

    if proof_method == "arc0060" and arc0060 is not None:
        return verify_arc0060_auth(
            wallet,
            data_b64=arc0060.data_b64,
            signature_b64=arc0060.signature_b64,
            authenticator_data_b64=arc0060.authenticator_data_b64,
            domain=arc0060.domain,
            expected_caip122=expected_caip122,
        )
    if proof_method == "arc0025_txn" and signed_txn_b64:
        return verify_auth_transaction(wallet, signing_message, signed_txn_b64)
    if proof_method == "legacy_message" and signature_b64:
        return verify_wallet_signature(wallet, signing_message, signature_b64)
    if proof_method == "signed_bytes" and signature_b64:
        return verify_signed_bytes(wallet, signing_message, signature_b64)
    return False


def issue_session_token(wallet: str) -> tuple[str, int]:
    """Mint a bearer session token for `wallet`, TTL x402_social_session_ttl_seconds.

    Returns (token, expires_at epoch). Raises SessionStoreError on a Redis
    failure -- session issuance has nothing durable to fall back to
    (design doc section 4.2: "Redis-only, deliberately").
    """
    token = secrets.token_urlsafe(48)
    ttl = settings.x402_social_session_ttl_seconds
    expires_at = int(time.time()) + ttl
    try:
        get_redis().setex(_session_key(token), ttl, wallet)
    except Exception as exc:
        raise SessionStoreError("session token store unavailable") from exc
    return token, expires_at


def session_wallet(token: str) -> str | None:
    """The wallet a bearer token belongs to, or None if absent/expired/unreadable.

    Fails to None (never raises) on a Redis error: a caller reading this
    treats None as "not authenticated", the same outcome as an actually
    expired or unknown token -- the safe default for a free-authenticated
    WRITE gate, unlike this module's own rate-limit gates which fail open
    (those protect against nuisance load; this protects against forging a
    session).
    """
    if not token:
        return None
    try:
        value = get_redis().get(_session_key(token))
    except Exception:
        return None
    return value if value else None
