"""In-memory x402 feature-request store for dev and tests."""

from __future__ import annotations

import threading

from app.modules.x402_features.models.domain import StoredFeatureRequest, StoredVote


class InMemoryFeatureStore:
    """In-memory x402 feature-request storage."""

    def __init__(self) -> None:
        """Start with an empty board."""
        self._items: dict[str, StoredFeatureRequest] = {}
        self._totals: dict[str, int] = {}
        self._votes: dict[str, list[StoredVote]] = {}
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

    def votes_for(self, request_id: str) -> list[StoredVote]:
        """Return a request's audit log. Test/dev helper -- not on the Protocol.

        The audit log has no public read surface by design (see StoredVote);
        this exists so tests can assert a vote was recorded without reaching
        into private attributes.
        """
        with self._lock:
            return list(self._votes.get(request_id, []))
