"""The follow graph: directed paid follow, free unfollow, and friend derivation (design doc section 2.4).

**Design call -- follow, not request/accept**, per the design doc's own
section 2.4: a follow is unilateral and paid, "friend" is defined as a
mutual follow, computed at read time from the two directed edge tables,
never a third materialized table.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.core.config import settings
from app.modules.x402_social.models.domain import (
    GRAPH_SCAN_LIMIT,
    FollowEdge,
    SocialError,
    not_registered_error,
)
from app.modules.x402_social.stores.base import SocialStore
from app.modules.x402_social.stores.factory import get_social_store

# wallet -> does this wallet have a registered profile. Bound in
# api/routes.py to profile_service.ProfileService.get(...) is not None --
# same decoupling precedent as post_service.IsRegisteredLookup (finding 4,
# 2026-security-audit).
IsRegisteredLookup = Callable[[str], bool]


class GraphService:
    """Follows, unfollows, and reads the follow graph (following/followers/friends)."""

    def __init__(
        self, store: SocialStore | None = None, *, is_registered: IsRegisteredLookup | None = None
    ) -> None:
        """Take explicit collaborators for tests; otherwise resolve the configured store lazily.

        `is_registered` has no lazy default: without one, `follow` always
        refuses as not_registered (fail closed) -- same contract as
        PostService's own `is_registered` seam.
        """
        self._store = store
        self._is_registered = is_registered

    @property
    def store(self) -> SocialStore:
        """The injected store, or the process-wide one built from settings."""
        return self._store or get_social_store()

    def follow(self, *, follower: str, followee: str, now: datetime | None = None) -> None:
        """Write a directed follow edge, idempotent (design doc section 2.4: no duplicate-follow rejection, unlike a reaction).

        Raises SocialError("cannot_follow_self", ..., 400) when
        `followee == follower` (finding 8, 2026-security-audit -- this
        docstring used to claim a self-follow was harmless because it could
        "never make you your own friend," which was WRONG: `upsert_follow`
        writes BOTH direction tables in one call, so a single self-follow
        satisfies the mutual-follow intersection with yourself and
        `friends()` would return the wallet itself). Cannot be checked
        before the payment gate -- `follower` is the settled payment's
        payer, only known after settlement (same constraint
        `not_group_member` documents) -- so this is caller-fault,
        settled-then-refused: payment kept, no refund.

        Raises SocialError("not_registered", ..., 403) if `follower` has no
        registered profile (finding 4, 2026-security-audit) -- POST-gate,
        checked first.
        """
        if self._is_registered is None or not self._is_registered(follower):
            raise not_registered_error()
        if followee == follower:
            raise SocialError(
                "cannot_follow_self",
                f"A wallet cannot follow itself ({follower}). Payment has settled but no follow "
                "edge was created.",
                http_status=400,
            )
        moment = now or datetime.now(tz=UTC)
        self.store.upsert_follow(
            follower=follower, followee=followee, created_at_epoch=int(moment.timestamp())
        )

    def unfollow(self, *, follower: str, followee: str) -> None:
        """Remove a directed follow edge. A no-op if it did not exist (free, so there is nothing to refuse)."""
        self.store.delete_follow(follower=follower, followee=followee)

    def following(self, wallet: str, *, limit: int) -> list[FollowEdge]:
        """Wallets `wallet` follows, most-recently-followed first, clamped to x402_social_max_results."""
        clamped = max(1, min(limit, settings.x402_social_max_results))
        return self.store.list_following(wallet, limit=clamped)

    def followers(self, wallet: str, *, limit: int) -> list[FollowEdge]:
        """Wallets that follow `wallet`, most-recently-followed first, clamped to x402_social_max_results."""
        clamped = max(1, min(limit, settings.x402_social_max_results))
        return self.store.list_followers(wallet, limit=clamped)

    def following_wallets(self, wallet: str, *, limit: int) -> list[str]:
        """Just the wallet strings `wallet` follows, most-recently-followed first -- the shape post_service.home_feed's fan-out needs."""
        clamped = max(1, min(limit, GRAPH_SCAN_LIMIT))
        return [edge.wallet for edge in self.store.list_following(wallet, limit=clamped)]

    def friends(self, wallet: str, *, limit: int) -> list[FollowEdge]:
        """Mutual follows: wallets `wallet` follows AND that follow `wallet` back, most-recently-(either-direction-)followed first.

        Intersection over a bounded scan of BOTH directed edge tables
        (GRAPH_SCAN_LIMIT each -- see domain.GRAPH_SCAN_LIMIT's own
        docstring): honest at competition scale, the same bounded trade the
        rest of this module makes. `created_at_epoch` on a returned edge is
        whichever of the two directions' timestamps is more recent (the
        later of "I followed them" / "they followed me" is when the
        friendship actually formed).
        """
        following = {
            edge.wallet: edge.created_at_epoch
            for edge in self.store.list_following(wallet, limit=GRAPH_SCAN_LIMIT)
        }
        followers = {
            edge.wallet: edge.created_at_epoch
            for edge in self.store.list_followers(wallet, limit=GRAPH_SCAN_LIMIT)
        }
        mutual = [
            FollowEdge(wallet=w, created_at_epoch=max(following[w], followers[w]))
            for w in following
            if w in followers
        ]
        mutual.sort(key=lambda e: (-e.created_at_epoch, e.wallet))
        clamped = max(1, min(limit, settings.x402_social_max_results))
        return mutual[:clamped]
