"""Posts, comments, reactions, and the bounded home-feed fan-out (design doc sections 2.2-2.4).

Group-membership authorization for a group post is checked via an injected
`membership_lookup` callable rather than an import of group_service -- the
same decoupling precedent x402_grading's `TagCandidateLookup` uses to avoid
depending on x402_directory: this module never has to know groups exist as
anything but a string id, and api/routes.py is the one place that wires the
real lookup in.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from app.core.config import settings
from app.modules.x402_social.models.domain import (
    COMMENT_SCAN_LIMIT,
    FEED_SOURCE_SCAN_LIMIT,
    MAX_COMMENT_BYTES,
    MAX_TAG_LENGTH,
    ReactionTotals,
    SocialError,
    StoredComment,
    StoredPost,
    not_registered_error,
)
from app.modules.x402_social.services.markdown_guard import validate_markdown_body
from app.modules.x402_social.stores.base import SocialStore
from app.modules.x402_social.stores.factory import get_social_store

# (group_id, wallet) -> is this wallet a member of this group. Bound in
# api/routes.py to group_service.GroupService.get_membership(...) is not
# None; a group that does not exist has no members either, so this one
# check also stands in for "does the group exist" without a second lookup.
MembershipLookup = Callable[[str, str], bool]

# wallet -> does this wallet have a registered profile. Bound in
# api/routes.py to profile_service.ProfileService.get(...) is not None --
# same decoupling precedent as MembershipLookup above, so this module never
# has to import profile_service (finding 4, 2026-security-audit).
IsRegisteredLookup = Callable[[str], bool]


def _new_post_or_comment_id() -> str:
    """A fresh timeuuid-compatible id for a post/comment, with a random node instead of this process's real MAC address.

    Plain `uuid.uuid1()` embeds the local NIC's MAC address in the node
    field of every generated id (that is the whole point of a version-1
    UUID's node field when the multicast bit is unset) -- minting public
    post_id/comment_id values straight off the network card leaks a stable
    hardware identifier of the server into every response, forever (finding
    5, 2026-security-audit). Forcing the multicast bit (`0x01` in the node
    field's top byte) on a randomly generated 48-bit node is the standard
    RFC 4122 way to mint a time-based UUID with no real MAC in it --
    Cassandra only validates the version nibble ("1"), which this preserves
    exactly (see stores/cassandra.py's `_uuid` docstring). No existing
    driver-level timeuuid-minting convention was found elsewhere in this
    codebase to reuse instead (checked x402_directory and neighboring
    modules) -- this keeps post_service.py store-agnostic (stdlib `uuid`
    only), consistent with it never otherwise importing the Cassandra
    driver.
    """
    return str(uuid.uuid1(node=random.getrandbits(48) | 0x010000000000))


def normalize_tags(raw: list[str]) -> list[str]:
    """Trimmed, de-duplicated, order-preserving, bounded tag list; raises SocialError if oversized.

    Same normalize-then-bound shape as profile_service.normalize_interests
    (order-preserving: a post's own tag order is worth keeping, this is not
    a search index).
    """
    seen: list[str] = []
    for item in raw:
        trimmed = item.strip().lower()
        if trimmed and trimmed not in seen:
            seen.append(trimmed)
    max_tags = settings.x402_social_max_tags
    if len(seen) > max_tags:
        raise SocialError(
            "invalid_request", f"tags must have at most {max_tags} items", http_status=400
        )
    for item in seen:
        if len(item) > MAX_TAG_LENGTH:
            raise SocialError(
                "invalid_request",
                f"each tag must be at most {MAX_TAG_LENGTH} characters",
                http_status=400,
            )
    return seen


class PostService:
    """Creates and reads posts and comments, records reactions, and assembles the bounded home-feed fan-out."""

    def __init__(
        self,
        store: SocialStore | None = None,
        *,
        membership_lookup: MembershipLookup | None = None,
        is_registered: IsRegisteredLookup | None = None,
    ) -> None:
        """Take explicit collaborators for tests; otherwise resolve the configured store lazily.

        `membership_lookup` has no lazy default: without one, posting into a
        group always refuses (see `create`) rather than silently allowing
        it -- the honest state for a service constructed with no way to
        check membership. `is_registered` (finding 4, 2026-security-audit)
        has the same no-lazy-default, fail-closed contract: without one,
        `create`/`react`/`add_comment` always refuse as not_registered
        rather than silently allowing an unregistered wallet through.
        """
        self._store = store
        self._membership_lookup = membership_lookup
        self._is_registered = is_registered

    def _require_registered(self, wallet: str) -> None:
        """Raise not_registered_error() unless `wallet` has a registered profile (finding 4, 2026-security-audit).

        POST-gate (payer only known after settlement, same constraint the
        group-membership check documents) -- called first thing from
        `create`, `react`, and `add_comment`.
        """
        if self._is_registered is None or not self._is_registered(wallet):
            raise not_registered_error()

    @property
    def store(self) -> SocialStore:
        """The injected store, or the process-wide one built from settings."""
        return self._store or get_social_store()

    # ----------------------------------------------------------------- #
    # Posts
    # ----------------------------------------------------------------- #
    def create(
        self,
        *,
        author: str,
        body_md: str,
        tags: list[str],
        group_id: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> StoredPost:
        """Store one new post and return it.

        Raises SocialError("invalid_request", ...) for an oversized/HTML
        body or too many/oversized tags -- callers check this BEFORE the
        payment gate (route-level, same "checkable without a payer, check
        first" precedent as x402_board.normalize_link) so a malformed post
        is a free 400.

        Raises SocialError("not_group_member", ..., 403) when `group_id` is
        set and the author is not a member of that group. This CANNOT be
        checked before the payment gate (design doc section 4.1: the author
        is the settled payment's payer, only known after settlement) --
        it is caller-fault, same settled-then-refused contract as
        x402_board_renew's ownership check, which has the identical
        "only knowable after the gate" constraint.

        Raises SocialError("not_registered", ..., 403) if `author` has no
        registered profile (finding 4, 2026-security-audit) -- checked
        first, before anything else.
        """
        self._require_registered(author)
        body = validate_markdown_body(body_md, max_bytes=settings.x402_social_post_max_bytes)
        clean_tags = normalize_tags(tags)
        clean_group_id = group_id.strip()
        if clean_group_id:
            is_member = self._membership_lookup is not None and self._membership_lookup(
                clean_group_id, author
            )
            if not is_member:
                raise SocialError(
                    "not_group_member",
                    "You must be a member of this group to post in it. Payment has settled "
                    "but no post was created -- join first via POST "
                    "/api/v1/x402/social/groups/{group_id}/join, then retry.",
                    http_status=403,
                )
        moment = now or datetime.now(tz=UTC)
        post = StoredPost(
            post_id=_new_post_or_comment_id(),
            author=author,
            group_id=clean_group_id,
            body_md=body,
            tags=clean_tags,
            created_at_epoch=int(moment.timestamp()),
            settlement_tx_id=settlement_tx_id,
        )
        self.store.insert_post(post)
        return post

    def get(self, post_id: str) -> StoredPost | None:
        """Return the canonical post for an id, or None if there is none (deleted posts ARE returned, with deleted=True)."""
        return self.store.get_post(post_id) if post_id else None

    def list_by_author(self, author: str, *, limit: int) -> list[StoredPost]:
        """Return one author's own NON-deleted posts newest-first, clamped to x402_social_max_results.

        Deleted (author-tombstoned) posts are filtered out here (finding 1,
        2026-security-audit): a deleted post is treated as fully gone from
        every free read surface, the same "deleted == not found" contract
        `add_comment`'s and `react`'s callers already apply at the route
        level (`post is None or post.deleted`) -- this is that same rule
        applied to a feed listing instead of a single-post lookup. Note this
        clamps BEFORE filtering (same as every other list_* here), so a
        feed with many deleted posts near the front can legitimately return
        fewer than `limit` live posts -- acceptable at this module's scale,
        same bounded-scan trade `home_feed` documents.
        """
        clamped = max(1, min(limit, settings.x402_social_max_results))
        posts = self.store.list_posts_by_author(author, limit=clamped)
        return [p for p in posts if not p.deleted]

    def list_group_feed(self, group_id: str, *, limit: int) -> list[StoredPost]:
        """Return one group's feed newest-first, clamped to x402_social_max_results."""
        clamped = max(1, min(limit, settings.x402_social_max_results))
        return self.store.list_group_feed(group_id, limit=clamped)

    def delete(self, post_id: str, *, wallet: str) -> StoredPost:
        """Author-only tombstone (DELETE /posts/{id}): sets deleted=true, never a row delete.

        Raises SocialError("not_found") if the post does not exist.
        Raises SocialError("not_post_author", ..., 403) if `wallet` did not
        author it. Idempotent: deleting an already-deleted post is a no-op
        success (the end state the caller wants already holds).
        """
        post = self.store.get_post(post_id)
        if post is None:
            raise SocialError("not_found", "No post with that id", http_status=404)
        if post.author != wallet:
            raise SocialError(
                "not_post_author", "Only the author of a post may delete it", http_status=403
            )
        if post.deleted:
            return post
        self.store.mark_post_deleted(post)
        return replace(post, deleted=True)

    def hide_in_group(self, post: StoredPost) -> StoredPost:
        """Set hidden_group=true on `post` (already read and already authorized by the caller -- see group_service.hide_post).

        Scoped to the group feed only (design doc section 2.7): the post
        keeps showing on the author's own feed.
        """
        self.store.mark_post_hidden_in_group(post)
        return replace(post, hidden_group=True)

    # ----------------------------------------------------------------- #
    # Comments
    # ----------------------------------------------------------------- #
    def add_comment(
        self,
        *,
        post_id: str,
        author: str,
        body_md: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> StoredComment:
        """Append one comment to a post's thread and return it.

        The caller checks the post exists BEFORE the payment gate (a 404 on
        an unknown post must be free) -- this method does not re-check, the
        same "existence check lives before the gate, the write lives after"
        split x402_features.vote's caller (exists()) uses.

        Raises SocialError("not_registered", ..., 403) if `author` has no
        registered profile (finding 4, 2026-security-audit).
        """
        self._require_registered(author)
        body = validate_markdown_body(body_md, max_bytes=MAX_COMMENT_BYTES)
        moment = now or datetime.now(tz=UTC)
        comment = StoredComment(
            post_id=post_id,
            comment_id=_new_post_or_comment_id(),
            author=author,
            body_md=body,
            created_at_epoch=int(moment.timestamp()),
            settlement_tx_id=settlement_tx_id,
        )
        self.store.insert_comment(comment)
        return comment

    def list_comments(self, post_id: str, *, limit: int) -> list[StoredComment]:
        """Return one post's comments oldest-first, clamped to x402_social_max_results."""
        clamped = max(1, min(limit, settings.x402_social_max_results))
        return self.store.list_comments(post_id, limit=clamped)

    def comment_count(self, post_id: str) -> tuple[int, bool]:
        """(count, truncated) for a post -- a bounded scan (COMMENT_SCAN_LIMIT + 1 rows) so the count saturates honestly rather than silently under-reporting.

        Same "ask for one extra row to detect truncation" precedent as
        x402_grading._scan.
        """
        rows = self.store.list_comments(post_id, limit=COMMENT_SCAN_LIMIT + 1)
        truncated = len(rows) > COMMENT_SCAN_LIMIT
        return (COMMENT_SCAN_LIMIT if truncated else len(rows)), truncated

    # ----------------------------------------------------------------- #
    # Reactions
    # ----------------------------------------------------------------- #
    def react(
        self,
        *,
        post_id: str,
        wallet: str,
        value: int,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> ReactionTotals:
        """Record one wallet's reaction to a post and return the resulting totals.

        Raises SocialError("already_reacted", ..., 409) if this wallet has
        already reacted to this post -- caller-fault, payment kept, no
        refund (design doc section 2.3: "the SAME settled-then-refused
        contract Phase S0's re-registration already established"). The
        caller checks the post exists BEFORE the payment gate, same split
        as add_comment.

        LWT-then-counter, in that order and never retried: try_add_reaction
        is the atomic slot-win; increment_reaction_total runs ONLY after it
        returns True, exactly once.

        Raises SocialError("not_registered", ..., 403) if `wallet` has no
        registered profile (finding 4, 2026-security-audit) -- checked
        first, before the reaction LWT is ever attempted.
        """
        self._require_registered(wallet)
        moment = now or datetime.now(tz=UTC)
        won = self.store.try_add_reaction(
            post_id=post_id,
            wallet=wallet,
            value=value,
            settlement_tx_id=settlement_tx_id,
            created_at_epoch=int(moment.timestamp()),
        )
        if not won:
            raise SocialError(
                "already_reacted",
                "This wallet has already reacted to this post. Payment has settled but no "
                "new reaction was recorded -- one reaction per wallet per post, forever.",
                http_status=409,
            )
        self.store.increment_reaction_total(post_id, value=value)
        return self.store.get_reaction_totals(post_id)

    def reaction_totals(self, post_id: str) -> ReactionTotals:
        """Return a post's current up/down totals, (0, 0) if never reacted to."""
        return self.store.get_reaction_totals(post_id)

    # ----------------------------------------------------------------- #
    # Home feed (design doc section 2.4)
    # ----------------------------------------------------------------- #
    def home_feed(self, *, followees: list[str], groups: list[str], limit: int) -> list[StoredPost]:
        """Merge the newest posts from `followees`' own feeds and `groups`' feeds, newest-first, clamped.

        Both lists are ALREADY bounded to at most x402_social_feed_fanout_limit
        by the caller (x402_social_feed route), which is also responsible for
        detecting and reporting `truncated_to` -- this method only assembles
        and sorts, the same split x402_grading's leaderboard makes between
        candidate selection and aggregation.

        Each source partition is read up to FEED_SOURCE_SCAN_LIMIT posts
        (bounded twice over: source count is capped, and each source's own
        contribution is capped), merged in memory, and sorted by created_at
        descending -- the same bounded-scan-then-sort trade
        x402_features.rank_by_demand makes, for the same reason (a
        live-updated cross-partition feed projection would need constant
        maintenance against writes racing on every followed wallet).

        Hidden-in-group and author-deleted posts are dropped; a group-hidden
        post is excluded from this merge (it is scoped OUT of that group's
        feed) even though it still exists on the author's own feed, which is
        exactly why it is not filtered out of the `followees` half.

        Deduped by post_id (finding 6, 2026-security-audit): a post whose
        author is BOTH directly followed AND a member of a joined group
        would otherwise be collected from both halves above and appear
        twice. Whichever copy is seen first (arbitrary -- dict insertion
        order over `collected`, which is not itself meaningfully ordered
        pre-sort) is kept; the two copies are equal in every field that
        matters to a reader (same post_id => same canonical content), so
        which one wins is not worth tracking further.
        """
        collected: list[StoredPost] = []
        for author in followees:
            collected.extend(self.store.list_posts_by_author(author, limit=FEED_SOURCE_SCAN_LIMIT))
        for group_id in groups:
            collected.extend(
                p
                for p in self.store.list_group_feed(group_id, limit=FEED_SOURCE_SCAN_LIMIT)
                if not p.hidden_group
            )
        deduped: dict[str, StoredPost] = {}
        for p in collected:
            deduped.setdefault(p.post_id, p)
        visible = [p for p in deduped.values() if not p.deleted]
        visible.sort(key=lambda p: (-p.created_at_epoch, p.post_id))
        clamped = max(1, min(limit, settings.x402_social_max_results))
        return visible[:clamped]
