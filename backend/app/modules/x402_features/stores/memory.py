"""In-memory x402 feature-request store for dev and tests."""

from __future__ import annotations

import threading

from app.modules.x402_features.models.domain import (
    CLAIMS_SCAN_LIMIT,
    ClaimSummary,
    StoredClaim,
    StoredFeatureRequest,
    StoredVote,
)


class InMemoryFeatureStore:
    """In-memory x402 feature-request storage."""

    def __init__(self) -> None:
        """Start with an empty board."""
        self._items: dict[str, StoredFeatureRequest] = {}
        self._totals: dict[str, int] = {}
        self._votes: dict[str, list[StoredVote]] = {}
        self._claims: dict[str, list[StoredClaim]] = {}
        # Lifecycle status (migration 119), kept as its own dict rather than a
        # mutated field on the StoredFeatureRequest object: a missing entry
        # means "no status set yet" (pending), matching how get_statuses
        # documents a missing id, and it mirrors the Cassandra backend's own
        # separate-column-read shape (get_statuses/update_status) rather than
        # coupling to a particular in-memory representation of the item.
        self._statuses: dict[str, str] = {}
        # Guards the vote total specifically. `self._totals[id] += 1` is a
        # read-modify-write, and CPython's bytecode for it is interruptible
        # between the read and the write -- two threads voting on the same
        # request can both read N and both write N+1, losing a paid vote. The
        # Cassandra backend gets this right with a counter column; this lock is
        # how the memory backend keeps the same promise, so a test written
        # against it is testing the real invariant rather than an accident of
        # the GIL.
        self._lock = threading.Lock()

    def insert(self, item: StoredFeatureRequest) -> None:
        """Store one new feature request."""
        self._items[item.request_id] = item

    def get(self, request_id: str) -> StoredFeatureRequest | None:
        """Return the request for an id, or None if there is none."""
        return self._items.get(request_id)

    def list_recent(self, *, limit: int) -> list[StoredFeatureRequest]:
        """Return requests newest-first, at most `limit` of them.

        Ties on created_at break by request_id ascending, matching the
        Cassandra table's (created_at DESC, request_id ASC) clustering order so
        tests see the same ordering as production.
        """
        ordered = sorted(self._items.values(), key=lambda i: (-i.created_at_epoch, i.request_id))
        return ordered[: max(0, limit)]

    def increment_vote_total(self, request_id: str) -> None:
        """Add one to a request's demand total, atomically."""
        with self._lock:
            self._totals[request_id] = self._totals.get(request_id, 0) + 1

    def get_vote_total(self, request_id: str) -> int:
        """Return a request's current demand total, 0 if it has never been voted on."""
        with self._lock:
            return self._totals.get(request_id, 0)

    def get_vote_totals(self, request_ids: list[str]) -> dict[str, int]:
        """Return demand totals for many requests at once, keyed by request id."""
        with self._lock:
            return {rid: self._totals[rid] for rid in request_ids if rid in self._totals}

    def append_vote(self, vote: StoredVote) -> None:
        """Append one vote to a request's audit log."""
        with self._lock:
            self._votes.setdefault(vote.request_id, []).append(vote)

    def append_claim(self, claim: StoredClaim) -> None:
        """Append one paid build claim to a request."""
        with self._lock:
            self._claims.setdefault(claim.request_id, []).append(claim)

    def get_claim_summaries(self, request_ids: list[str]) -> dict[str, ClaimSummary]:
        """Return (count, latest claimer) per request, newest-first and bounded like Cassandra.

        Sorted (claimed_at DESC, claimer ASC) and cut at CLAIMS_SCAN_LIMIT to
        match the Cassandra table's clustering order and read bound, so tests
        see the same saturation as production.
        """
        with self._lock:
            summaries: dict[str, ClaimSummary] = {}
            for rid in request_ids:
                claims = self._claims.get(rid)
                if not claims:
                    continue
                ordered = sorted(claims, key=lambda c: (-c.claimed_at_epoch, c.claimer))
                ordered = ordered[:CLAIMS_SCAN_LIMIT]
                summaries[rid] = ClaimSummary(count=len(ordered), latest_claimer=ordered[0].claimer)
            return summaries

    def delete(self, request_id: str) -> bool:
        """Remove one request and its claims; keep its vote total and audit log (as Cassandra does)."""
        with self._lock:
            existed = self._items.pop(request_id, None) is not None
            self._claims.pop(request_id, None)
            self._statuses.pop(request_id, None)
        return existed

    def has_claimed(self, request_id: str, wallet: str) -> bool:
        """Whether `wallet` has ever claimed this request, within the same bound get_claim_summaries uses.

        Sorted and cut the same way (claimed_at DESC, claimer ASC, LIMIT
        CLAIMS_SCAN_LIMIT) so a claim outside that window is "not found" here
        too -- the same documented degradation the claim count already
        accepts, rather than a stricter or looser check depending on which
        method happens to look.
        """
        with self._lock:
            claims = self._claims.get(request_id, [])
            ordered = sorted(claims, key=lambda c: (-c.claimed_at_epoch, c.claimer))[
                :CLAIMS_SCAN_LIMIT
            ]
            return any(c.claimer == wallet for c in ordered)

    def update_status(self, request_id: str, status: str) -> None:
        """Set a request's lifecycle status."""
        with self._lock:
            self._statuses[request_id] = status

    def get_statuses(self, request_ids: list[str]) -> dict[str, str]:
        """Return each request's lifecycle status, keyed by request id. Missing = pending."""
        with self._lock:
            return {rid: self._statuses[rid] for rid in request_ids if rid in self._statuses}

    def claims_for(self, request_id: str) -> list[StoredClaim]:
        """Return a request's claims. Test/dev helper -- not on the Protocol."""
        with self._lock:
            return list(self._claims.get(request_id, []))

    def votes_for(self, request_id: str) -> list[StoredVote]:
        """Return a request's audit log. Test/dev helper -- not on the Protocol.

        The audit log has no public read surface by design (see StoredVote);
        this exists so tests can assert a vote was recorded without reaching
        into private attributes.
        """
        with self._lock:
            return list(self._votes.get(request_id, []))
