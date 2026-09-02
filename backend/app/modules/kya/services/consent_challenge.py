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


def consume_consent_challenge(wallet_address: str) -> ConsentChallenge | None:
    """Atomically pop the pending challenge for `wallet_address`, or None if absent.

    GETDEL so two parallel enrolls cannot both redeem the same nonce. Raises
    ConsentStoreError when Redis itself fails (fail closed: enrollment must
    not proceed without a consumed challenge).
    """
    try:
        raw = get_redis().getdel(_key(wallet_address))
    except Exception as exc:
        raise ConsentStoreError("consent challenge store unavailable") from exc
    if not raw:
        return None
    data = serialization.loads(raw)
    return ConsentChallenge(nonce=str(data["nonce"]), expires_at=int(data["expires_at"]))
