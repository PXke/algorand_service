"""Redis-backed session and nonce storage for wallet auth."""

from __future__ import annotations

import time
from dataclasses import dataclass

import redis

from app.core import serialization
from app.core.config import settings


@dataclass
class SessionRecord:
    """A wallet's active login session."""

    wallet_address: str
    issued_at_epoch: int
    expires_in_epoch: int


class SessionStore:
    """Redis-backed session and nonce storage for wallet auth."""

    def __init__(self) -> None:
        """Connect to the shared Redis instance backing nonces and sessions."""
        self._redis = redis.from_url(settings.redis_url, decode_responses=True)

    def allow_nonce_issue(self, wallet_address: str, *, max_per_minute: int = 15) -> bool:
        """Rate-limit nonce issuance per wallet, returning False once the per-minute cap is hit."""
        key = f"auth:nonce_rate:{wallet_address}"
        count = int(self._redis.incr(key))
        if count == 1:
            self._redis.expire(key, 60)
        return count <= max_per_minute

    def set_nonce_challenge(self, wallet_address: str, nonce: str, challenge_json: str) -> None:
        """Store the pending nonce challenge JSON for a (wallet, nonce) pair, until it's popped or expires."""
        self._redis.setex(
            self._nonce_key(wallet_address, nonce),
            settings.nonce_ttl_seconds,
            challenge_json,
        )

    def pop_nonce_challenge(self, wallet_address: str, nonce: str) -> str | None:
        """Atomically pop the pending nonce challenge JSON for a (wallet, nonce) pair, or None if absent.

        Keyed by (wallet, nonce), not wallet alone (2026-09-07 security
        review, finding 2 -- the same bug already found and fixed in
        x402_social's session_service.py: a wallet-only key means ANY
        verify request naming that wallet, garbage nonce/signature
        included, would GETDEL whatever real challenge the wallet owner
        had just minted -- an attacker who knows only the public admin
        address (on-chain, not secret) could grief every login attempt by
        hammering verify with no rate limit on this route. Keying by the
        unguessable nonce too means a wrong-nonce attempt simply misses,
        leaving the legitimate pending challenge untouched. GETDEL so two
        parallel verify requests for the same (wallet, nonce) still cannot
        both read it.
        """
        return self._redis.getdel(self._nonce_key(wallet_address, nonce))

    @staticmethod
    def _nonce_key(wallet_address: str, nonce: str) -> str:
        return f"auth:nonce:{wallet_address}:{nonce}"

    def set_session(self, token: str, wallet_address: str) -> SessionRecord:
        """Create and store a new session for a wallet, returning the session record."""
        now = int(time.time())
        rec = SessionRecord(
            wallet_address=wallet_address,
            issued_at_epoch=now,
            expires_in_epoch=now + settings.session_ttl_seconds,
        )
        self._redis.setex(
            f"auth:session:{token}",
            settings.session_ttl_seconds,
            serialization.dumps(rec.__dict__),
        )
        return rec

    def get_session(self, token: str) -> SessionRecord | None:
        """Look up an active session by token, or None if absent/expired.

        Successful lookups slide the Redis TTL so active users are not logged
        out at a fixed wall-clock from login.
        """
        key = f"auth:session:{token}"
        raw = self._redis.get(key)
        if not raw:
            return None
        data = serialization.loads(raw)
        now = int(time.time())
        data["expires_in_epoch"] = now + settings.session_ttl_seconds
        self._redis.setex(key, settings.session_ttl_seconds, serialization.dumps(data))
        return SessionRecord(**data)

    def delete_session(self, token: str) -> None:
        """Delete a session by token."""
        self._redis.delete(f"auth:session:{token}")
