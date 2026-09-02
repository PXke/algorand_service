"""Register / read / edit x402 social agent profiles (Phase S0).

The registered wallet is always the caller's identity as proven by the
surrounding auth mechanism (design doc section 4): for registration, the
route passes `wallet=PaymentResult.payer` (§4.1 -- the payment IS the
identity proof); for an edit, the route passes the wallet a valid bearer
session resolved to (§4.2). This module never reads a wallet out of a
request body -- see api/routes.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.core.config import settings
from app.modules.x402_social.models.domain import (
    MAX_BIO_LEN,
    MAX_EMOJI_BYTES,
    MAX_INTEREST_LEN,
    MAX_INTERESTS,
    MAX_LOCATION_LEN,
    MAX_MISSION_LEN,
    MAX_NAME_LEN,
    AgentProfile,
    SocialError,
)
from app.modules.x402_social.stores.base import SocialStore
from app.modules.x402_social.stores.factory import get_social_store


def normalize_interests(raw: list[str]) -> list[str]:
    """Trimmed, de-duplicated, bounded interests list; raises SocialError if oversized.

    Order-preserving (unlike x402_directory's sorted-set tags): interests
    are a short self-declared list, not a search index, so the agent's own
    ordering is worth keeping.
    """
    seen: list[str] = []
    for item in raw:
        trimmed = item.strip()
        if trimmed and trimmed not in seen:
            seen.append(trimmed)
    if len(seen) > MAX_INTERESTS:
        raise SocialError(
            "invalid_request", f"interests must have at most {MAX_INTERESTS} items", http_status=400
        )
    for item in seen:
        if len(item) > MAX_INTEREST_LEN:
            raise SocialError(
                "invalid_request",
                f"each interest must be at most {MAX_INTEREST_LEN} characters",
                http_status=400,
            )
    return seen


def validate_profile_fields(
    *,
    name: str,
    bio: str,
    mission: str,
    location: str,
    interests: list[str],
    emoji: str,
) -> tuple[str, str, str, str, list[str], str]:
    """Bound and normalize every self-declared profile field, raising SocialError on the first violation.

    Called by the route BEFORE the payment gate on POST /register (so a
    malformed profile is a free 400) and unconditionally by both
    ProfileService.register and ProfileService.edit (the durable guard on
    the columns, same "checked at the edge AND at the service" precedent as
    x402_directory's validate_tags/validate_category).
    """
    name = name.strip()
    if not name or len(name) > MAX_NAME_LEN:
        raise SocialError(
            "invalid_request", f"name must be 1-{MAX_NAME_LEN} characters", http_status=400
        )
    bio = bio.strip()
    if len(bio) > MAX_BIO_LEN:
        raise SocialError(
            "invalid_request", f"bio must be at most {MAX_BIO_LEN} characters", http_status=400
        )
    mission = mission.strip()
    if len(mission) > MAX_MISSION_LEN:
        raise SocialError(
            "invalid_request",
            f"mission must be at most {MAX_MISSION_LEN} characters",
            http_status=400,
        )
    location = location.strip()
    if len(location) > MAX_LOCATION_LEN:
        raise SocialError(
            "invalid_request",
            f"location must be at most {MAX_LOCATION_LEN} characters",
            http_status=400,
        )
    emoji = emoji.strip()
    if len(emoji.encode("utf-8")) > MAX_EMOJI_BYTES:
        raise SocialError(
            "invalid_request", f"emoji must be at most {MAX_EMOJI_BYTES} bytes", http_status=400
        )
    return name, bio, mission, location, normalize_interests(interests), emoji


class ProfileService:
    """Registers and edits agent profiles, keyed by wallet."""

    def __init__(self, store: SocialStore | None = None) -> None:
        """Take an explicit store for tests; otherwise resolve the configured one lazily."""
        self._store = store

    @property
    def store(self) -> SocialStore:
        """The injected store, or the process-wide one built from settings."""
        return self._store or get_social_store()

    def register(
        self,
        *,
        wallet: str,
        name: str,
        bio: str,
        mission: str,
        location: str,
        interests: list[str],
        emoji: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> AgentProfile:
        """Create a new agent profile for `wallet`.

        Raises SocialError("wallet_already_registered", ..., http_status=409)
        if a profile for this wallet already exists -- caller-fault: the
        route wraps this in modules/x402/paid_request.run_with_refund, whose
        PlatformError contract keeps the payment and never refunds a
        SocialError (see that function's own docstring). One profile per
        wallet, forever -- design doc section 2.1: "Re-registering an
        existing wallet is a caller-fault PlatformError -> payment kept,
        409 -- same settled-then-refused contract as a directory relist by
        a non-owner."

        First-time path is the store's atomic insert_agent_if_absent (a
        Cassandra lightweight transaction on the real backend), NOT a read
        followed by an upsert -- two concurrent first-time registrations of
        the SAME wallet (two payments racing) cannot both silently succeed;
        exactly one wins and the other is refused here with its payment
        already settled, same race-safety precedent as
        x402_directory.services.listing_service.ListingService.create.
        """
        moment = now or datetime.now(tz=UTC)
        epoch = int(moment.timestamp())
        name, bio, mission, location, interests, emoji = validate_profile_fields(
            name=name, bio=bio, mission=mission, location=location, interests=interests, emoji=emoji
        )
        profile = AgentProfile(
            wallet=wallet,
            name=name,
            bio=bio,
            mission=mission,
            location=location,
            interests=interests,
            emoji=emoji,
            created_at_epoch=epoch,
            updated_at_epoch=epoch,
            settlement_tx_id=settlement_tx_id,
        )
        if not self.store.insert_agent_if_absent(profile):
            raise SocialError(
                "wallet_already_registered",
                "This wallet already has a profile. Payment has settled but no new "
                "profile was created -- use PATCH /api/v1/x402/social/profile "
                "(free, session-authenticated) to edit it instead.",
                http_status=409,
            )
        return profile

    def get(self, wallet: str) -> AgentProfile | None:
        """The full stored profile for a wallet, or None if not registered."""
        return self.store.get_agent(wallet)

    def list_recent(self, *, limit: int) -> list[AgentProfile]:
        """Registered agents newest-first (thin projection), clamped to x402_social_max_results."""
        clamped = max(1, min(limit, settings.x402_social_max_results))
        return self.store.list_recent_agents(limit=clamped)

    def edit(
        self,
        *,
        wallet: str,
        name: str | None,
        bio: str | None,
        mission: str | None,
        location: str | None,
        interests: list[str] | None,
        emoji: str | None,
        now: datetime | None = None,
    ) -> AgentProfile:
        """Patch an existing profile's self-declared fields; a None field is left unchanged.

        Raises SocialError("not_found", ...) if the wallet has no profile --
        a valid session token alone does not create one; registration
        (POST /register, paid) is the only creation path. created_at_epoch
        and settlement_tx_id are never touched by an edit (see AgentProfile's
        own docstring): this is a free route with nothing new settled.
        """
        existing = self.store.get_agent(wallet)
        if existing is None:
            raise SocialError(
                "not_found", "No profile for this wallet -- register first", http_status=404
            )
        moment = now or datetime.now(tz=UTC)
        merged_name, merged_bio, merged_mission, merged_location, merged_interests, merged_emoji = (
            validate_profile_fields(
                name=existing.name if name is None else name,
                bio=existing.bio if bio is None else bio,
                mission=existing.mission if mission is None else mission,
                location=existing.location if location is None else location,
                interests=existing.interests if interests is None else interests,
                emoji=existing.emoji if emoji is None else emoji,
            )
        )
        updated = replace(
            existing,
            name=merged_name,
            bio=merged_bio,
            mission=merged_mission,
            location=merged_location,
            interests=merged_interests,
            emoji=merged_emoji,
            updated_at_epoch=int(moment.timestamp()),
        )
        self.store.upsert_agent(updated)
        return updated
