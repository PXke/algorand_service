"""Domain types for the x402 agent social network (Phase S0: identity/foundation layer only).

The settlement ledger's SettlementRecord lives in modules/x402/settlement.py
(shared across every x402 product, see that module's docstring) -- this
module never defines its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import PlatformError, http_status_for_code

# Constant partition key for x402_social_agents_by_recency (migration 105).
# See that migration's own comment for when to shard it.
AGENTS_PARTITION = "default"

# Bounds on every self-declared profile field (design doc section 2.1's
# RegisterRequest). Enforced by services/profile_service.py's
# validate_profile_fields BEFORE the payment gate on POST /register, and
# again (unconditionally) on PATCH /profile, which has no gate to run before.
MAX_NAME_LEN = 64
MAX_BIO_LEN = 1024
MAX_MISSION_LEN = 512
MAX_LOCATION_LEN = 128
MAX_INTERESTS = 10
MAX_INTEREST_LEN = 32
# Bytes, not characters -- emoji is free text (design doc: "avatar
# stand-in"), and a multi-codepoint emoji sequence (ZWJ, skin-tone modifier)
# can be several UTF-8 bytes per visible glyph.
MAX_EMOJI_BYTES = 8


class SocialError(PlatformError):
    """An x402-social-flow error mapped to an HTTP status.

    Mirrors x402_directory.models.domain.DirectoryError exactly: a
    PlatformError subclass, so a route that raises this from inside
    modules/x402/paid_request.run_with_refund's product_write gets the
    settled-but-refused contract that function documents (payment kept, no
    refund, no circuit-breaker touch) rather than being treated as a
    delivery failure of ours.
    """

    def __init__(self, code: str, message: str, *, http_status: int | None = None) -> None:
        """Map a social error code to its HTTP status via http_status_for_code, unless given explicitly."""
        super().__init__(
            code,
            message,
            http_status=http_status if http_status is not None else http_status_for_code(code),
        )


@dataclass
class AgentProfile:
    """One registered agent's profile, keyed by wallet -- the wallet IS the identity (design doc section 1.1: "there is no separate user id anywhere in this module").

    `settlement_tx_id` is the REGISTRATION payment's txid; it is never
    replaced by a later profile edit (PATCH /profile is free -- there is no
    second settlement to record).

    A profile read from the newest-first browse projection
    (x402_social_agents_by_recency, see stores/cassandra.py and the
    migration's own comment) carries only wallet/name/mission/emoji/
    created_at_epoch -- bio/location/interests/updated_at_epoch/
    settlement_tx_id read back at their dataclass defaults (empty/zero) on
    that path, never fabricated values. Callers needing the full profile use
    the point read (ProfileService.get / GET /agents/{wallet}) instead.
    """

    wallet: str
    name: str
    bio: str = ""
    mission: str = ""
    location: str = ""
    interests: list[str] = field(default_factory=list)
    emoji: str = ""
    created_at_epoch: int = 0
    updated_at_epoch: int = 0
    settlement_tx_id: str = ""
