"""msgspec.Struct request bodies for the x402 agent social network (Phase S0).

Decoded through app/core/serialization.py like every other route in this
backend -- pydantic is fully removed (CLAUDE.md section 5 / global rule).
Response bodies are plain dicts built by api/routes.py's own `_agent_json`,
the same convention x402_directory and kya already use (their own response
Structs in app/schemas.py -- X402ListingItem, EnrollResponse -- exist as
documentation; the actual routes return dicts). Field bounds mirror
models/domain.py's MAX_* constants exactly -- msgspec.Meta only stops an
oversized value from ever reaching the service layer; the durable guard on
the same bound lives in profile_service.validate_profile_fields, which every
caller of ProfileService (not just these two routes) goes through.
"""

from __future__ import annotations

from typing import Annotated, Literal

import msgspec
from msgspec import Meta, field

from app.modules.auth.models.schemas import Arc0060Proof
from app.modules.x402_social.models.domain import (
    MAX_BIO_LEN,
    MAX_EMOJI_BYTES,
    MAX_INTEREST_LEN,
    MAX_INTERESTS,
    MAX_LOCATION_LEN,
    MAX_MISSION_LEN,
    MAX_NAME_LEN,
)

# Same shape as app.schemas.WalletAddress -- not imported from there to keep
# this module's own request validation self-contained (a 58-char Algorand
# address is a stable, universal constant, not a piece of shared behavior
# whose drift would matter -- unlike Arc0060Proof, which IS imported below
# because it is the actual ARC-60 proof shape auth/utils/arc0060_verify.py
# expects, and duplicating that would be exactly the kind of copy CLAUDE.md
# section 3 forbids).
WalletAddress = Annotated[str, Meta(min_length=58, max_length=58)]

ProofMethod = Literal["arc0025_txn", "arc0060", "legacy_message", "signed_bytes"]


class ChallengeRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /auth/challenge."""

    wallet: WalletAddress


class SessionRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /auth/session -- the signed challenge.

    Same accepted proof_method set and the same required-field-per-method
    validation as app.schemas.VerifyRequest (the newspaper login's own
    request body), because this reuses the exact same verifiers
    (services/session_service.py).
    """

    wallet: WalletAddress
    nonce: str
    proof_method: ProofMethod = "signed_bytes"
    signature_b64: str | None = None
    signed_txn_b64: str | None = None
    arc0060: Arc0060Proof | None = None

    def __post_init__(self) -> None:
        """Require the proof field matching `proof_method`, mirroring app.schemas.VerifyRequest."""
        if self.proof_method == "arc0060":
            if self.arc0060 is None:
                raise ValueError("arc0060 proof is required when proof_method is arc0060")
        elif self.proof_method == "arc0025_txn" and not self.signed_txn_b64:
            raise ValueError("signed_txn_b64 is required when proof_method is arc0025_txn")
        elif self.proof_method in ("legacy_message", "signed_bytes") and not self.signature_b64:
            raise ValueError(f"signature_b64 is required when proof_method is {self.proof_method}")


class RegisterRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /register (design doc section 2.1). The registered wallet is NEVER a field here -- it is the settled payment's payer (section 4.1); see api/routes.py."""

    name: Annotated[str, Meta(min_length=1, max_length=MAX_NAME_LEN)]
    bio: Annotated[str, Meta(max_length=MAX_BIO_LEN)] = ""
    mission: Annotated[str, Meta(max_length=MAX_MISSION_LEN)] = ""
    location: Annotated[str, Meta(max_length=MAX_LOCATION_LEN)] = ""
    interests: Annotated[
        list[Annotated[str, Meta(max_length=MAX_INTEREST_LEN)]], Meta(max_length=MAX_INTERESTS)
    ] = field(default_factory=list)
    emoji: Annotated[str, Meta(max_length=MAX_EMOJI_BYTES)] = ""


class ProfilePatchRequest(msgspec.Struct, kw_only=True):
    """Request body for PATCH /profile -- every field optional, omitted means "leave unchanged" (mirrors app.schemas.ArticlePatchRequest's None-means-untouched convention). An explicit empty string DOES clear a field -- None and "" are distinguishable here, unlike a sentinel-free partial-update shape would allow."""

    name: Annotated[str, Meta(min_length=1, max_length=MAX_NAME_LEN)] | None = None
    bio: Annotated[str, Meta(max_length=MAX_BIO_LEN)] | None = None
    mission: Annotated[str, Meta(max_length=MAX_MISSION_LEN)] | None = None
    location: Annotated[str, Meta(max_length=MAX_LOCATION_LEN)] | None = None
    interests: (
        Annotated[
            list[Annotated[str, Meta(max_length=MAX_INTEREST_LEN)]], Meta(max_length=MAX_INTERESTS)
        ]
        | None
    ) = None
    emoji: Annotated[str, Meta(max_length=MAX_EMOJI_BYTES)] | None = None
