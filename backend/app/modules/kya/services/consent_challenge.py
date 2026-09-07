"""Single-use KYA consent challenges (nonce + expiry) in Redis."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from app.core import serialization
from app.core.config import settings
from app.core.redis_client import get_redis

_KEY_PREFIX = "algorand:kya:consent:"


class ConsentStoreError(Exception):
    """Redis was unreachable while issuing or consuming a consent challenge."""


@dataclass(frozen=True, slots=True)
class ConsentChallenge:
    """The nonce and unix expiry a wallet must embed in the signed consent message."""

    nonce: str
    expires_at: int


def _key(wallet_address: str) -> str:
    return f"{_KEY_PREFIX}{wallet_address}"


def issue_consent_challenge(wallet_address: str) -> ConsentChallenge:
    """Mint a fresh nonce, persist it until `kyc_consent_ttl_seconds`, and return it.

    Raises ConsentStoreError when Redis itself fails: the client must not be
    handed a message we cannot later consume.
    """
    nonce = secrets.token_urlsafe(24)
    ttl = settings.kyc_consent_ttl_seconds
    expires_at = int(time.time()) + ttl
    try:
        get_redis().setex(
            _key(wallet_address),
            ttl,
            serialization.dumps({"nonce": nonce, "expires_at": expires_at}),
        )
    except Exception as exc:
        raise ConsentStoreError("consent challenge store unavailable") from exc
    return ConsentChallenge(nonce=nonce, expires_at=expires_at)


def peek_consent_challenge(wallet_address: str) -> ConsentChallenge | None:
    """Non-destructively read the pending challenge for `wallet_address`, or None if absent.

    Deliberately NOT a pop (2026-09-07 security review, finding 2 -- same bug
    class as x402_social's session_service.py: this route's client never
    sends the nonce back, only wallet_address + a signature, so the
    wallet+nonce Redis-key fix used for /auth login and x402 social sessions
    doesn't transfer directly here without a wire-format change. Instead,
    the caller must verify the signature against the peeked challenge FIRST
    and only call discard_consent_challenge on success -- a garbage
    signature can then never pop, and so never invalidate, the real pending
    challenge, closing the same griefing hole without touching the request
    schema. Raises ConsentStoreError when Redis itself fails (fail closed:
    enrollment must not proceed on an unconfirmed store).
    """
    try:
        raw = get_redis().get(_key(wallet_address))
    except Exception as exc:
        raise ConsentStoreError("consent challenge store unavailable") from exc
    if not raw:
        return None
    data = serialization.loads(raw)
    return ConsentChallenge(nonce=str(data["nonce"]), expires_at=int(data["expires_at"]))


def discard_consent_challenge(wallet_address: str) -> None:
    """Delete the pending challenge for `wallet_address` -- call only after its signature has verified.

    Safe to call more than once (plain DEL): two concurrent valid
    submissions may both pass verification and both enroll (enrollment
    itself overwrites idempotently, see kyc_enroll's own docstring) and both
    reach this call -- that is a narrower, already-accepted race (the same
    "two parallel valid redeemers" case the old GETDEL used to trade off),
    not a new one. Raises ConsentStoreError when Redis itself fails.
    """
    try:
        get_redis().delete(_key(wallet_address))
    except Exception as exc:
        raise ConsentStoreError("consent challenge store unavailable") from exc
