"""Groups: paid create (LWT name claim), paid join, free leave, owner/moderator stewardship (design doc sections 2.5-2.7).

Section 2.7 is explicit that group-owner moderation of a group's own space
(hiding a post from that group's feed, removing a member) does NOT wait on
section 5's (unapproved) community moderation design -- it never touches
anything platform-wide and never touches a `hidden_platform`/standing
concept that does not exist in this codebase yet.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

from app.core.config import settings
from app.modules.x402_social.models.domain import (
    GRAPH_SCAN_LIMIT,
    GROUP_ROLE_MEMBER,
    GROUP_ROLE_MODERATOR,
    GROUP_ROLE_OWNER,
    MAX_GROUP_DESCRIPTION_LEN,
    MAX_GROUP_NAME_LEN,
    SocialError,
    StoredGroup,
    StoredMembership,
    StoredPost,
)
from app.modules.x402_social.services.post_service import PostService
from app.modules.x402_social.stores.base import SocialStore
from app.modules.x402_social.stores.factory import get_social_store


def normalize_group_name(raw: str) -> str:
    """Trim, validate, and case-fold a group name for both display and the name-claim key.

    Case-folded because two groups differing only by case would visually
    collide in every listing -- the LWT claim must consider "DeFi-Signals"
    and "defi-signals" the same name.
    """
    name = raw.strip()
    if not name or len(name) > MAX_GROUP_NAME_LEN:
        raise SocialError(
            "invalid_request", f"name must be 1-{MAX_GROUP_NAME_LEN} characters", http_status=400
        )
    return name


def group_id_for(name_norm: str) -> str:
    """Identity of one group: the hex SHA-256 of its normalized (case-folded) name.

    Same "hash the thing that must be unique" precedent as x402_board's
    placement_id -- the id is reproducible from the name alone.
    """
    return hashlib.sha256(name_norm.encode()).hexdigest()


class GroupService:
    """Creates, joins, leaves, and moderates groups."""

    def __init__(
        self, store: SocialStore | None = None, *, post_service: PostService | None = None
    ) -> None:
        """Take explicit collaborators for tests; otherwise resolve the configured ones lazily."""
        self._store = store
        self._post_service = post_service

    @property
    def store(self) -> SocialStore:
        """The injected store, or the process-wide one built from settings."""
        return self._store or get_social_store()

    @property
    def post_service(self) -> PostService:
        """The injected PostService, or a fresh one bound to this same store -- used only by hide_post."""
        return self._post_service or PostService(self._store)

    # ----------------------------------------------------------------- #
    # Create / read
    # ----------------------------------------------------------------- #
    def create(
        self,
        *,
        owner: str,
        name: str,
        description: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> StoredGroup:
        """Claim a group name and store the group, owner as the sole initial member.

        Raises SocialError("group_name_taken", ..., 409) if the LWT name
        claim loses -- caller-fault, payment kept, no refund (design doc
        section 2.6: "a name collision after paying is caller-fault").
        """
        clean_name = normalize_group_name(name)
        name_norm = clean_name.lower()
        clean_description = description.strip()[:MAX_GROUP_DESCRIPTION_LEN]
        group_id = group_id_for(name_norm)
        if not self.store.try_claim_group_name(name_norm=name_norm, group_id=group_id):
            raise SocialError(
                "group_name_taken",
                "This group name is already taken. Payment has settled but no group was "
                "created -- pick a different name.",
                http_status=409,
            )
        moment = now or datetime.now(tz=UTC)
        epoch = int(moment.timestamp())
        group = StoredGroup(
            group_id=group_id,
            name=clean_name,
            description=clean_description,
            owner=owner,
            created_at_epoch=epoch,
            settlement_tx_id=settlement_tx_id,
        )
        self.store.insert_group(group)
        self.store.upsert_membership(
            StoredMembership(
                group_id=group_id,
                wallet=owner,
                role=GROUP_ROLE_OWNER,
                joined_at_epoch=epoch,
                settlement_tx_id=settlement_tx_id,
            )
        )
        return group

    def get(self, group_id: str) -> StoredGroup | None:
        """Return the canonical group for an id, or None if there is none."""
        return self.store.get_group(group_id) if group_id else None

    def list_recent(self, *, limit: int) -> list[StoredGroup]:
        """Return groups newest-first, clamped to x402_social_max_results."""
        clamped = max(1, min(limit, settings.x402_social_max_results))
        return self.store.list_groups_recent(limit=clamped)

    def get_membership(self, group_id: str, wallet: str) -> StoredMembership | None:
        """Return one wallet's membership in one group, or None if they are not a member."""
        return self.store.get_membership(group_id, wallet) if group_id and wallet else None

    def is_member(self, group_id: str, wallet: str) -> bool:
        """Whether `wallet` currently belongs to `group_id`. Bound as post_service.MembershipLookup in api/routes.py."""
        return self.get_membership(group_id, wallet) is not None

    def membership_group_ids(self, wallet: str, *, limit: int) -> list[str]:
        """Group ids `wallet` has joined, most-recently-joined first -- the shape post_service.home_feed's fan-out needs.

        Bounded scan (see domain.GRAPH_SCAN_LIMIT's own docstring: these
        tables are not clustered by recency), sorted by joined_at
        descending, then sliced to `limit`.
        """
        clamped = max(1, min(limit, GRAPH_SCAN_LIMIT))
        memberships = self.store.list_memberships(wallet, limit=GRAPH_SCAN_LIMIT)
        ordered = sorted(memberships, key=lambda m: (-m.joined_at_epoch, m.group_id))
        return [m.group_id for m in ordered[:clamped]]

    # ----------------------------------------------------------------- #
    # Join / leave
    # ----------------------------------------------------------------- #
    def join(
        self, *, group_id: str, wallet: str, settlement_tx_id: str, now: datetime | None = None
    ) -> StoredMembership:
        """Join a group as a plain member. Idempotent: an existing membership (any role) is returned unchanged, never downgraded.

        Raises SocialError("not_found") if the group does not exist -- the
        caller checks this BEFORE the payment gate too (a free 404), this
        is the defense-in-depth re-check.
        """
        group = self.store.get_group(group_id)
        if group is None:
            raise SocialError("not_found", "No group with that id", http_status=404)
        existing = self.store.get_membership(group_id, wallet)
        if existing is not None:
            return existing
        moment = now or datetime.now(tz=UTC)
        membership = StoredMembership(
            group_id=group_id,
            wallet=wallet,
            role=GROUP_ROLE_MEMBER,
            joined_at_epoch=int(moment.timestamp()),
            settlement_tx_id=settlement_tx_id,
        )
        self.store.upsert_membership(membership)
        return membership

    def leave(self, *, group_id: str, wallet: str) -> None:
        """Leave a group (free, session). Raises SocialError("not_found") if not a member; SocialError("owner_cannot_leave", ..., 400) if `wallet` owns the group (transfer is not in v1, design doc section 2.6)."""
        membership = self.store.get_membership(group_id, wallet)
        if membership is None:
            raise SocialError("not_found", "You are not a member of this group", http_status=404)
        if membership.role == GROUP_ROLE_OWNER:
            raise SocialError(
                "owner_cannot_leave",
                "The group owner cannot leave their own group in v1 (ownership transfer is "
                "not supported yet).",
                http_status=400,
            )
        self.store.delete_membership(group_id, wallet)

    # ----------------------------------------------------------------- #
    # Owner/moderator stewardship (design doc section 2.7 -- scoped, never
    # platform-wide, does not wait on section 5)
    # ----------------------------------------------------------------- #
    def _require_owner(self, group_id: str, actor_wallet: str) -> StoredGroup:
        group = self.store.get_group(group_id)
        if group is None:
            raise SocialError("not_found", "No group with that id", http_status=404)
        if group.owner != actor_wallet:
            raise SocialError(
                "not_group_owner", "Only the group's owner may do this", http_status=403
            )
        return group

    def _require_owner_or_moderator(self, group_id: str, actor_wallet: str) -> StoredMembership:
        group = self.store.get_group(group_id)
        if group is None:
            raise SocialError("not_found", "No group with that id", http_status=404)
        membership = self.store.get_membership(group_id, actor_wallet)
        if membership is None or membership.role not in (GROUP_ROLE_OWNER, GROUP_ROLE_MODERATOR):
            raise SocialError(
                "not_group_owner_or_moderator",
                "Only the group's owner or a moderator may do this",
                http_status=403,
            )
        return membership

    def set_moderator(
        self, *, group_id: str, actor_wallet: str, target_wallet: str
    ) -> StoredMembership:
        """Promote a member to moderator. Owner only. Raises SocialError("target_not_member", ..., 404) if the target has no membership."""
        self._require_owner(group_id, actor_wallet)
        target = self.store.get_membership(group_id, target_wallet)
        if target is None:
            raise SocialError(
                "target_not_member", "That wallet is not a member of this group", http_status=404
            )
        if target.role == GROUP_ROLE_OWNER:
            return target
        self.store.set_membership_role(group_id, target_wallet, role=GROUP_ROLE_MODERATOR)
        return replace(target, role=GROUP_ROLE_MODERATOR)

    def unset_moderator(
        self, *, group_id: str, actor_wallet: str, target_wallet: str
    ) -> StoredMembership:
        """Demote a moderator back to a plain member. Owner only. Raises SocialError("cannot_change_owner_role", ..., 400) if the target is the owner."""
        self._require_owner(group_id, actor_wallet)
        target = self.store.get_membership(group_id, target_wallet)
        if target is None:
            raise SocialError(
                "target_not_member", "That wallet is not a member of this group", http_status=404
            )
        if target.role == GROUP_ROLE_OWNER:
            raise SocialError(
                "cannot_change_owner_role",
                "The group owner's role cannot be changed",
                http_status=400,
            )
        self.store.set_membership_role(group_id, target_wallet, role=GROUP_ROLE_MEMBER)
        return replace(target, role=GROUP_ROLE_MEMBER)

    def hide_post(self, *, group_id: str, actor_wallet: str, post_id: str) -> StoredPost:
        """Owner/moderator hides one post from THIS group's feed only (design doc section 2.7).

        Raises SocialError("not_found") if the group or the post does not
        exist, or if the post was not posted in this group (a target for a
        different group's post id is treated identically to "not found" --
        it gives away nothing about the other group's content).
        """
        self._require_owner_or_moderator(group_id, actor_wallet)
        post = self.post_service.get(post_id)
        if post is None or post.group_id != group_id:
            raise SocialError("not_found", "No post with that id in this group", http_status=404)
        return self.post_service.hide_in_group(post)

    def remove_member(self, *, group_id: str, actor_wallet: str, target_wallet: str) -> None:
        """Owner/moderator revokes a member's membership. Raises SocialError("cannot_remove_owner", ..., 400) if the target is the owner."""
        self._require_owner_or_moderator(group_id, actor_wallet)
        target = self.store.get_membership(group_id, target_wallet)
        if target is None:
            raise SocialError(
                "target_not_member", "That wallet is not a member of this group", http_status=404
            )
        if target.role == GROUP_ROLE_OWNER:
            raise SocialError(
                "cannot_remove_owner", "The group owner cannot be removed", http_status=400
            )
        self.store.delete_membership(group_id, target_wallet)
