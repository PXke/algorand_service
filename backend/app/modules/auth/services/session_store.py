"""Redis-backed session and nonce storage for wallet auth."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import redis

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

    def set_nonce_challenge(self, wallet_address: str, challenge_json: str) -> None:
        """Store the pending nonce challenge JSON for a wallet, until it's popped or expires."""
        self._redis.setex(
            f"auth:nonce:{wallet_address}",
            settings.nonce_ttl_seconds,
            challenge_json,
        )

    def pop_nonce_challenge(self, wallet_address: str) -> str | None:
        """Pop and return the pending nonce challenge JSON for a wallet, or None if absent."""
        key = f"auth:nonce:{wallet_address}"
        raw = self._redis.get(key)
        if raw:
            self._redis.delete(key)
        return raw

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
            json.dumps(rec.__dict__),
        )
        return rec

    def get_session(self, token: str) -> SessionRecord | None:
        """Look up an active session by token, or None if absent/expired."""
        raw = self._redis.get(f"auth:session:{token}")
        if not raw:
            return None
        data = json.loads(raw)
        return SessionRecord(**data)

    def delete_session(self, token: str) -> None:
        """Delete a session by token."""
        self._redis.delete(f"auth:session:{token}")
