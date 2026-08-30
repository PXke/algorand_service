"""Domain types for paid feature requests and the paid votes cast on them.

The settlement ledger is shared infrastructure and lives in
modules/x402/settlement.py -- nothing feature-board-specific about it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import PlatformError, http_status_for_code

# Constant partition key for x402_feature_requests_by_recency. See migration
# 092 for why the whole board lives in one partition and when to shard it.
FEATURES_PARTITION = "default"


class FeatureError(PlatformError):
    """A feature-board error mapped to an HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        """Map a feature-board error code to its HTTP status via http_status_for_code."""
        super().__init__(code, message, http_status=http_status_for_code(code))


@dataclass
class StoredFeatureRequest:
    """One paid feature request: what somebody paid to ask for.

    No term_end, unlike StoredPlacement. A board tile is rented advertising
    that must expire; a feature request is a durable statement of demand whose
    whole value is accumulating votes over time. Expiring one would silently
    destroy the demand signal its voters paid to build.

    `submitter` is the paying wallet -- a public Algorand address the payer
    themselves put on-chain by paying, already in the settlement ledger, and
    not personal data. It is deliberately absent from the FREE browse surface
    and present on the PAID demand surface, along with the vote counts.
    """

    request_id: str
    title: str
    description: str
    submitter: str
    settlement_tx_id: str
    created_at_epoch: int


@dataclass
class StoredVote:
    """One paid vote, appended to a request's audit log.

    Never publicly readable: this exists for audit and abuse forensics ("did
    one wallet manufacture this request's entire demand?"), not as a product
    surface. The public demand number is the counter total, not a count of
    these rows -- see FeatureStore.add_vote.
    """

    request_id: str
    voter: str
    settlement_tx_id: str
    voted_at_epoch: int


@dataclass
class RankedFeatureRequest:
    """One request plus its demand total, as served by the PAID demand read."""

    request: StoredFeatureRequest
    vote_total: int
