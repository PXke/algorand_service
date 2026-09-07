"""Private messages (DMs) between two registered agents (migration 122, operator ask 2026-09-07: "Did we do the DM in our social network?").

**Free, session-authenticated -- NOT a paid payer-is-identity write** like
every other Phase S1 write in this module (post/comment/react/follow/group
create/join). Every one of those uses the settled payment's payer as the
acting identity because the payment itself is the anti-sybil floor (design
doc section 4.1). A DM has no analogous marketplace-visible product to
price -- it is never public, never searchable, never part of any feed or
ranking -- so there is nothing for a price to protect except the recipient's
inbox from spam, and CLAUDE.md section 9's "rate limit every free endpoint
per wallet AND per IP" is the tool this codebase already has for exactly
that. Rate-limiting a SENDER pre-action requires knowing the sender's
identity BEFORE the write runs; a paid route's payer is only known AFTER
settlement (the same constraint post_service.create's not_group_member and
graph_service.follow's not_registered checks already document as
architecturally POST-gate-only) -- so a paid DM-send could only rate-limit
by wallet after the sender had already paid, which defeats the point. A
free, bearer-session-authenticated write (the same shape PATCH /profile,
DELETE follow, and DELETE group membership already use) sidesteps this
entirely: the caller's wallet is known from the session token before
anything runs, so both a per-wallet and a per-IP budget
(services/rate_limit.py's dm_send_wallet_rate_limited / dm_send_ip_rate_limited)
can refuse a request for free, before any write is attempted.

Both participants must already be registered (profile_service.get is not
None) -- same fail-closed `is_registered` seam precedent as PostService/
GraphService/GroupService. Unlike GraphService.follow, the RECIPIENT is
checked too (not just the sender): a DM to an unregistered wallet has no
way to ever be read (there is no non-DM surface on this module that a
wallet without a session can reach), so allowing it would just be a
silent-loss trap for the sender, unlike a follow of an unregistered wallet
(which at least still shows up in the follower's own following-list).
"""

from __future__ import annotations

import hashlib
import random
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.modules.x402_social.models.domain import (
    DM_CONVERSATION_SCAN_LIMIT,
    DM_PREVIEW_LEN,
    MAX_DM_BODY_BYTES,
    StoredDmConversation,
    StoredDmMessage,
    cannot_message_self_error,
    not_registered_error,
)
from app.modules.x402_social.services.markdown_guard import validate_markdown_body
from app.modules.x402_social.stores.base import SocialStore
from app.modules.x402_social.stores.factory import get_social_store

# wallet -> does this wallet have a registered profile. Bound in
# api/routes.py to profile_service.ProfileService.get(...) is not None --
# same decoupling precedent as PostService.IsRegisteredLookup and
# GraphService.IsRegisteredLookup (finding 4, 2026-security-audit).
IsRegisteredLookup = Callable[[str], bool]


def conversation_id_for(wallet_a: str, wallet_b: str) -> str:
    """Identity of one two-party conversation: the hex SHA-256 of the two wallets, sorted then joined.

    Same "hash the thing that must be unique" precedent as
    group_service.group_id_for -- reproducible from the two wallets alone,
    order-independent, so both participants (in either call order) always
    resolve to the same partition.
    """
    ordered = sorted((wallet_a, wallet_b))
    return hashlib.sha256(f"{ordered[0]}:{ordered[1]}".encode()).hexdigest()


def _new_message_id() -> str:
    """A fresh timeuuid-compatible id for a DM message, with a random node instead of this process's real MAC address.

    Identical rationale and mechanism to post_service._new_post_or_comment_id
    (finding 5, 2026-security-audit: a plain uuid.uuid1() embeds the local
    NIC's MAC address) -- not imported from there since post_service is a
    Phase S1 module this one deliberately stays decoupled from (this
    module's own precedent: DMs are their own concern, not a post/comment
    variant), so the same three-line mint is duplicated rather than adding a
    cross-phase import for one helper.
    """
    return str(uuid.uuid1(node=random.getrandbits(48) | 0x010000000000))


def _preview(body: str) -> str:
    """Truncate a message body to DM_PREVIEW_LEN for the conversation-list row -- never the full body (see that constant's own docstring)."""
    if len(body) <= DM_PREVIEW_LEN:
        return body
    return body[:DM_PREVIEW_LEN]


class DmService:
    """Sends and reads private messages between two registered agents."""

    def __init__(
        self, store: SocialStore | None = None, *, is_registered: IsRegisteredLookup | None = None
    ) -> None:
        """Take explicit collaborators for tests; otherwise resolve the configured store lazily.

        `is_registered` has no lazy default: without one, `send` always
        refuses as not_registered (fail closed) -- same contract as
        PostService's/GraphService's own `is_registered` seam.
        """
        self._store = store
        self._is_registered = is_registered

    @property
    def store(self) -> SocialStore:
        """The injected store, or the process-wide one built from settings."""
        return self._store or get_social_store()

    def send(
        self, *, sender: str, recipient: str, body: str, now: datetime | None = None
    ) -> StoredDmMessage:
        """Store one new message and update both participants' conversation-list rows.

        Raises SocialError("not_registered", ..., 403) if `sender` or
        `recipient` has no registered profile -- checked first, before
        anything else (both directions, unlike GraphService.follow -- see
        this module's own docstring for why the recipient is checked here).

        Raises SocialError("cannot_message_self", ..., 400) when
        `recipient == sender`, mirroring GraphService.follow's identical
        self-follow refusal.

        Store-before-mark ordering (CLAUDE.md section 2 invariant 2): the
        canonical message row is written first; the two (idempotent,
        overwrite-in-place) conversation-list rows are written after -- a
        failure between the two leaves the message durably stored and
        resolvable via list_conversation, missing only from one or both
        conversation-list browse rows, which the NEXT message between the
        same two wallets repairs.
        """
        if self._is_registered is None or not self._is_registered(sender):
            raise not_registered_error()
        if self._is_registered is None or not self._is_registered(recipient):
            raise not_registered_error()
        if recipient == sender:
            raise cannot_message_self_error(sender)
        clean_body = validate_markdown_body(body, max_bytes=MAX_DM_BODY_BYTES)

        moment = now or datetime.now(tz=UTC)
        moment_epoch = int(moment.timestamp())
        conversation_id = conversation_id_for(sender, recipient)
        message = StoredDmMessage(
            conversation_id=conversation_id,
            message_id=_new_message_id(),
            sender=sender,
            recipient=recipient,
            body=clean_body,
            created_at_epoch=moment_epoch,
        )
        self.store.insert_dm_message(message)

        preview = _preview(clean_body)
        self.store.upsert_dm_conversation(
            StoredDmConversation(
                wallet=sender,
                peer_wallet=recipient,
                conversation_id=conversation_id,
                last_message_at_epoch=moment_epoch,
                last_sender=sender,
                last_message_preview=preview,
            )
        )
        self.store.upsert_dm_conversation(
            StoredDmConversation(
                wallet=recipient,
                peer_wallet=sender,
                conversation_id=conversation_id,
                last_message_at_epoch=moment_epoch,
                last_sender=sender,
                last_message_preview=preview,
            )
        )
        return message

    def list_conversation(
        self, wallet_a: str, wallet_b: str, *, limit: int
    ) -> list[StoredDmMessage]:
        """Return the messages between two wallets, newest-first, clamped to DM_CONVERSATION_SCAN_LIMIT.

        The caller (api/routes.py) is responsible for checking the
        SESSION-authenticated caller is one of `wallet_a`/`wallet_b` before
        calling this -- this method itself has no notion of "who is
        allowed to read this," it only resolves the shared conversation_id
        and reads its log.
        """
        clamped = max(1, min(limit, DM_CONVERSATION_SCAN_LIMIT))
        conversation_id = conversation_id_for(wallet_a, wallet_b)
        return self.store.list_dm_messages(conversation_id, limit=clamped)

    def list_conversations(self, wallet: str, *, limit: int) -> list[StoredDmConversation]:
        """Return `wallet`'s own conversations, most-recently-active first, clamped.

        Bounded single-partition scan (LIMIT DM_CONVERSATION_SCAN_LIMIT)
        sorted by last_message_at in Python -- same bounded-scan-then-sort
        trade graph_service.following()/followers() makes for "most
        recently followed" (see domain.DM_CONVERSATION_SCAN_LIMIT's own
        docstring).
        """
        rows = self.store.list_dm_conversations(wallet, limit=DM_CONVERSATION_SCAN_LIMIT)
        ordered = sorted(rows, key=lambda r: (-r.last_message_at_epoch, r.peer_wallet))
        clamped = max(1, min(limit, DM_CONVERSATION_SCAN_LIMIT))
        return ordered[:clamped]
