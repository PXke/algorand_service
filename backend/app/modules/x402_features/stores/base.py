"""Storage interface for feature requests and the paid votes cast on them."""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_features.models.domain import (
    ClaimSummary,
    StoredClaim,
    StoredFeatureRequest,
    StoredVote,
)


class FeatureStore(Protocol):
    """Storage interface for the x402 feature-request board."""

    def insert(self, item: StoredFeatureRequest) -> None:
        """Store one new feature request, recency projection included."""
        ...

    def get(self, request_id: str) -> StoredFeatureRequest | None:
        """Return the request for an id, or None if there is none."""
        ...

    def list_recent(self, *, limit: int) -> list[StoredFeatureRequest]:
        """Return requests newest-first, at most `limit` of them."""
        ...

    def increment_vote_total(self, request_id: str) -> None:
        """Add one to a request's demand total, atomically.

        Must never lose a concurrent increment. Two votes settling at the same
        instant are two payments, and a demand board that silently merges them
        into one is under-reporting paid signal -- so this is a real atomic
        add-one, not a read-modify-write. See the Cassandra backend (a counter
        column) and the memory backend (a lock) for how each keeps that
        promise.
        """
        ...

    def get_vote_total(self, request_id: str) -> int:
        """Return a request's current demand total, 0 if it has never been voted on."""
        ...

    def get_vote_totals(self, request_ids: list[str]) -> dict[str, int]:
        """Return demand totals for many requests at once, keyed by request id.

        Its own method rather than a loop over get_vote_total at the call site:
        the demand ranking needs every candidate's total, and issuing those as
        sequential round trips would make the paid read's latency scale with
        the size of the board. Ids with no votes may be omitted; the caller
        treats a missing id as 0.
        """
        ...

    def append_vote(self, vote: StoredVote) -> None:
        """Append one vote to a request's audit log.

        Separate from increment_vote_total so the service, not each backend,
        owns the order the two happen in and what a failure of each means.
        """
        ...

    def append_claim(self, claim: StoredClaim) -> None:
        """Append one paid build claim to a request. Never replaces an earlier one."""
        ...

    def get_claim_summaries(self, request_ids: list[str]) -> dict[str, ClaimSummary]:
        """Return (count, latest claimer) for many requests at once, keyed by request id.

        Batched for the same reason get_vote_totals is: both read surfaces
        need it for a whole page. Each request's claims are read newest-first
        and LIMITed to CLAIMS_SCAN_LIMIT, so `count` saturates there. Ids with
        no claims may be omitted; the caller treats a missing id as none.
        """
        ...

    def delete(self, request_id: str) -> bool:
        """Remove one request, its recency row and its claims. False if it did not exist.

        Admin-only. The vote counter and the vote audit log are deliberately
        kept: a Cassandra counter cannot be safely deleted and re-incremented,
        and the audit log exists for abuse forensics -- a removed request is
        exactly the case where "who paid to vote on this" still matters.
        """
        ...

    def has_claimed(self, request_id: str, wallet: str) -> bool:
        """Whether `wallet` has ever claimed this request (migration 119's authorization check).

        Reuses the same bounded per-request claims read get_claim_summaries
        does (CLAIMS_SCAN_LIMIT): a claim older than that bound is not found,
        the same documented degradation the claim count already accepts.
        Claims are rare (a builder declares once, not per unit of demand), so
        this never needs a dedicated lookup table.
        """
        ...

    def update_status(self, request_id: str, status: str) -> None:
        """Set a request's lifecycle status (see domain.FEATURE_STATUS_*).

        Called by the service after a durable write has already happened
        (store before mark, CLAUDE.md section 2): after append_claim for a
        claim, or after the authorization check for an explicit completion.
        The caller is responsible for passing a request id known to exist --
        this never creates a request.
        """
        ...

    def get_statuses(self, request_ids: list[str]) -> dict[str, str]:
        """Return each request's lifecycle status, keyed by request id.

        Batched for the same reason get_vote_totals and get_claim_summaries
        are: both read surfaces need it for a whole page. An id with no
        status set yet (no claim, never completed) may be omitted; the
        caller treats a missing id as domain.FEATURE_STATUS_PENDING.
        """
        ...
