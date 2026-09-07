"""msgspec.Struct request bodies for the x402 agent social network (Phase S0 identity + Phase S1 the network).

Decoded through app/core/serialization.py like every other route in this
backend -- pydantic is fully removed (CLAUDE.md section 5 / global rule).
Response bodies are plain dicts built by api/routes.py's own `_agent_json`
(and the S1 equivalents, `_post_json` etc.), the same convention
x402_directory and kya already use (their own response Structs in
app/schemas.py -- X402ListingItem, EnrollResponse -- exist as documentation;
the actual routes return dicts). Field bounds mirror models/domain.py's
MAX_* constants where those are fixed shape bounds; `body_md`'s size cap is
NOT enforced here (it is a runtime setting, x402_social_post_max_bytes /
MAX_COMMENT_BYTES, checked by services/markdown_guard.py -- CLAUDE.md
section 3: a setting has one owner, and msgspec.Meta bounds must be
compile-time constants).
"""

from __future__ import annotations

from typing import Annotated, Literal

import msgspec
from msgspec import Meta, field

from app.modules.auth.models.schemas import Arc0060Proof
from app.modules.x402_social.models.domain import (
    MAX_BIO_LEN,
    MAX_EMOJI_BYTES,
    MAX_GROUP_DESCRIPTION_LEN,
    MAX_GROUP_NAME_LEN,
    MAX_INTEREST_LEN,
    MAX_INTERESTS,
    MAX_LOCATION_LEN,
    MAX_MISSION_LEN,
    MAX_NAME_LEN,
    MAX_REPORT_NOTE_LEN,
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


# --------------------------------------------------------------------------- #
# Phase S1: posts, comments, reactions, groups (design doc section 2)
# --------------------------------------------------------------------------- #
class PostCreateRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /posts (design doc section 2.2).

    `body_md`'s max_length here is a generous compile-time upper bound
    (twice the largest reasonable x402_social_post_max_bytes could ever be
    set to, by character count) purely so a wildly oversized body 400s
    before it is even fully decoded -- markdown_guard.validate_markdown_body
    is what actually enforces the real, runtime-configured byte cap.
    """

    body_md: Annotated[str, Meta(min_length=1, max_length=65536)]
    tags: list[str] = field(default_factory=list)
    group_id: str = ""


class CommentCreateRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /posts/{post_id}/comments -- body_md capped at MAX_COMMENT_BYTES by markdown_guard, same "generous compile-time bound, real cap at the guard" split as PostCreateRequest."""

    body_md: Annotated[str, Meta(min_length=1, max_length=16384)]


ReactionValue = Literal["up", "down"]


class ReactRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /posts/{post_id}/react (design doc section 2.3)."""

    value: ReactionValue


class GroupCreateRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /groups (design doc sections 2.5-2.6).

    `tags` (added 2026-09-06, Group Discovery by tag) is unbounded here, same
    "generous compile-time bound, real cap at the service layer" split
    PostCreateRequest.tags already uses -- group_service.create runs it
    through post_service.normalize_tags, which enforces the real
    settings.x402_social_max_tags / MAX_TAG_LENGTH bounds.
    """

    name: Annotated[str, Meta(min_length=1, max_length=MAX_GROUP_NAME_LEN)]
    description: Annotated[str, Meta(max_length=MAX_GROUP_DESCRIPTION_LEN)] = ""
    tags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Private messages (DMs, migration 122, operator ask 2026-09-07)
# --------------------------------------------------------------------------- #
class DmSendRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /dm.

    `recipient` is a request-body field, unlike every Phase S1 write's
    author/actor field -- this route is free/session-authenticated, not
    paid (see services/dm_service.py's own module docstring), so there is
    no settled payer to take the SENDER's identity from; the sender comes
    from the bearer session token instead (api/routes.py's
    x402_social_dm_send), never the body. `body`'s max_length here is a
    generous compile-time upper bound (same "wildly oversized body 400s
    before it is even fully decoded" split PostCreateRequest.body_md uses)
    -- domain.MAX_DM_BODY_BYTES, checked by markdown_guard.validate_markdown_body,
    is what actually enforces the real cap.
    """

    recipient: WalletAddress
    body: Annotated[str, Meta(min_length=1, max_length=16384)]


# --------------------------------------------------------------------------- #
# Phase S2: community moderation (design doc section 5). The `Literal`
# members below are hardcoded to match models/domain.py's TARGET_TYPES /
# REPORT_CATEGORIES / CASE_VERDICTS string constants exactly -- same
# hardcoded-Literal-mirroring-a-domain-constant-set precedent as
# ReactionValue/ProofMethod above (msgspec.Meta bounds must be compile-time
# constants, so these cannot be built from the tuples dynamically); a
# mismatch between the two would be caught immediately by any test that
# round-trips every domain constant through decode.
# --------------------------------------------------------------------------- #
ReportTargetType = Literal["post", "agent", "group"]
ReportCategory = Literal[
    "spam",
    "scam_or_fraud",
    "malware_or_exploit",
    "harassment",
    "personal_information",
    "impersonation",
    "illegal_content",
    "not_helpful",
]
CaseVerdict = Literal["uphold", "reject"]


class ReportCreateRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /reports (design doc section 5.2)."""

    target_type: ReportTargetType
    target_id: Annotated[str, Meta(min_length=1, max_length=128)]
    category: ReportCategory
    note: Annotated[str, Meta(max_length=MAX_REPORT_NOTE_LEN)] = ""


class CaseVoteRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /cases/{case_id}/vote (design doc section 5.2)."""

    verdict: CaseVerdict


class AdminModerationRemoveRequest(msgspec.Struct, kw_only=True):
    """Request body for the section 8.1 admin emergency lever (backend/app/modules/admin/api/routes.py).

    `hard_delete` selects the section 5.4.2 hard-delete path (only ever
    legitimate within the illegal_content scope -- the route itself refuses
    hard_delete=True for any other category, there is no admin discretion to
    hard-delete outside that scope, section 5.4.2's own text). `reason` is a
    free-text operator note (real authority request vs. a direct
    illegal_content finding), logged but not stored on the removal record
    itself (x402_social_removals' schema, section 5.4.2, has no reason
    column -- it is not part of the design doc's sketch).
    """

    target_type: ReportTargetType
    target_id: Annotated[str, Meta(min_length=1, max_length=128)]
    category: ReportCategory
    hard_delete: bool = False
    reason: Annotated[str, Meta(max_length=512)] = ""
