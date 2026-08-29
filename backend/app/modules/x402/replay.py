"""Replay protection for the x402 payment header.

Shared across every paid module. Moved here from x402_directory 2026-08-30
for the same reason as settlement.py: this was never actually
directory-specific, and every future paid module needs the identical
claim-before-gate behaviour.
"""

from __future__ import annotations

import hashlib
import logging

from app.core.config import settings
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

_REPLAY_PREFIX = "algorand:x402:spent:"


def _replay_key(header: str) -> str:
    """Redis key for one payment header, hashed so no signature material is stored."""
    return _REPLAY_PREFIX + hashlib.sha256(header.encode("utf-8")).hexdigest()


def claim_payment(header: str) -> tuple[str | None, bool]:
    """Atomically claim a payment header as being spent right now.

    Returns (key_to_release_on_failure, already_seen). SET NX is the claim, so
    two concurrent requests carrying the same header cannot both proceed to
    settle. The key outlives the request by x402_replay_ttl_seconds, which is
    required to be >= 2x the facilitator's own HTTP timeout.

    Fails OPEN: a Redis outage must not take a paid endpoint offline. The
    facilitator and the chain remain the authoritative double-spend guard; this
    is defence in depth in front of them, not the only line.
    """
    if not header:
        return None, False
    try:
        claimed = get_redis().set(
            _replay_key(header), "1", nx=True, ex=settings.x402_replay_ttl_seconds
        )
    except Exception:
        logger.warning(
            "x402 replay check failed; failing open and letting the payment through",
            exc_info=True,
        )
        return None, False
    if not claimed:
        return None, True
    return _replay_key(header), False


def release_claim(key: str | None) -> None:
    """Release a claim whose payment never settled, so a valid retry is not burned."""
    if not key:
        return
    try:
        get_redis().delete(key)
    except Exception:
        logger.warning(
            "x402 replay claim %s could not be released; a retry of this payment header "
            "will be rejected as a replay until it expires",
            key,
            exc_info=True,
        )
