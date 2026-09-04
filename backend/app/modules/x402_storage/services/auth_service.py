"""Challenge issuance and signature verification for x402 storage's free wallet-signature-authenticated routes.

Deliberately NOT a session/bearer-token system: a disaster-recovery restore
must not depend on session state that might be exactly what was lost, so
every protected call (GET list, GET one, DELETE) re-proves wallet control
from scratch, right there in that request -- see api/routes.py's own
docstring on the auth transport shape.

Style-mirrors x402_social/services/session_service.py's issue_challenge /
verify_challenge_signature (same short-TTL Redis-backed single-use nonce,
same fail-closed-on-a-Redis-error contract) but is deliberately NOT an import
of that module (CLAUDE.md: keep x402_storage self-contained) and deliberately
smaller: only the two generic signature primitives named in the design --
app.modules.auth.utils.algorand_verify.verify_wallet_signature (a raw-message
signature) and verify_signed_bytes (algosdk signBytes/Pera signData
convention) -- no CAIP-122/arc0060/txn-proof support, which social's own
module needs for its browser-wallet login flow and this module does not.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from app.core import serialization
from app.core.redis_client import get_redis
from app.modules.auth.utils.algorand_verify import verify_signed_bytes, verify_wallet_signature

_CHALLENGE_KEY_PREFIX = "algorand:x402storage:challenge:"
# Single-use signing window. Short on purpose: unlike a bearer session, this
# challenge is meant to be signed and redeemed in one round trip, not carried
# around -- see the module docstring on why there is no session here at all.
_CHALLENGE_TTL_SECONDS = 300
_SIGNING_STATEMENT = "PXke x402 storage auth"

PROOF_METHODS = ("legacy_message", "signed_bytes")


class StorageAuthStoreError(Exception):
    """Redis was unreachable while issuing or consuming a challenge -- the caller must not proceed on an unconfirmed store."""


@dataclass(frozen=True, slots=True)
class StorageChallenge:
    """The nonce and signing message a wallet must sign to prove control for one protected call."""

    nonce: str
    signing_message: str
    expires_at: int


def _challenge_key(wallet: str, nonce: str) -> str:
    """Keyed by (wallet, nonce), not wallet alone -- same reasoning as x402_social's own _challenge_key.

    A wallet-only key would let a request carrying ANY nonce for a given
    wallet (garbage included, no proof of key possession required) GETDEL
    whatever real challenge that wallet's owner just minted, griefing their
    in-flight auth for free. Keying by the nonce too means a caller who does
    not know the real (secrets.token_urlsafe(24), effectively unguessable)
    nonce can never address, let alone clobber, the real pending challenge.
    """
    return f"{_CHALLENGE_KEY_PREFIX}{wallet}:{nonce}"


def issue_challenge(wallet: str) -> StorageChallenge:
    """Mint a fresh nonce, persist it until _CHALLENGE_TTL_SECONDS, and return it.

    Raises StorageAuthStoreError when Redis itself fails: the caller must not
    be handed a challenge that cannot later be consumed.
    """
    nonce = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + _CHALLENGE_TTL_SECONDS
    signing_message = (
        f"{_SIGNING_STATEMENT}\nwallet: {wallet}\nnonce: {nonce}\nexpires_at: {expires_at}"
    )
    try:
        get_redis().setex(
            _challenge_key(wallet, nonce),
            _CHALLENGE_TTL_SECONDS,
            serialization.dumps(
                {"nonce": nonce, "signing_message": signing_message, "expires_at": expires_at}
            ),
        )
    except Exception as exc:
        raise StorageAuthStoreError("storage auth challenge store unavailable") from exc
    return StorageChallenge(nonce=nonce, signing_message=signing_message, expires_at=expires_at)


def verify_challenge_signature(
    *, wallet: str, nonce: str, proof_method: str, signature_b64: str
) -> bool:
    """Consume the pending challenge for `(wallet, nonce)` and verify the signature over it.

    GETDEL (single-use: two concurrent callers cannot both redeem it, and a
    captured signature cannot be replayed against a later request -- this IS
    the "fresh per-request proof" the design calls for, not a convenience on
    top of one). A request carrying a wrong/garbage nonce for `wallet` simply
    misses the key and returns False, WITHOUT touching that wallet's real
    pending challenge.

    `proof_method` is one of PROOF_METHODS: "legacy_message" (raw-message
    signature, verify_wallet_signature) or "signed_bytes" (algosdk
    signBytes/Pera signData convention, verify_signed_bytes). Any other value,
    or a blank signature, is a flat False.

    Raises StorageAuthStoreError on a Redis failure (fail closed: a caller
    must not be treated as authenticated on an unconfirmed store).
    """
    try:
        raw = get_redis().getdel(_challenge_key(wallet, nonce))
    except Exception as exc:
        raise StorageAuthStoreError("storage auth challenge store unavailable") from exc
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
    if not signature_b64:
        return False

    signing_message = str(stored["signing_message"])
    if proof_method == "legacy_message":
        return verify_wallet_signature(wallet, signing_message, signature_b64)
    if proof_method == "signed_bytes":
        return verify_signed_bytes(wallet, signing_message, signature_b64)
    return False
