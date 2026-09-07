"""Domain types for feature requests and the paid votes cast on them.

The settlement ledger is shared infrastructure and lives in
modules/x402/settlement.py -- nothing feature-board-specific about it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import PlatformError, http_status_for_code

# Constant partition key for x402_feature_requests_by_recency. See migration
# 092 for why the whole board lives in one partition and when to shard it.
FEATURES_PARTITION = "default"

# Bound on the per-request claims read behind claims_count (migration 098).
# A module constant rather than a setting (config.py is outside this change):
# claims are rare relative to votes -- a builder declares once, not per unit
# of demand -- so a count that saturates at 100 loses nothing real, and it
# keeps the batched read on the FREE browse surface strictly bounded.
CLAIMS_SCAN_LIMIT = 100

# A request's status (migration 119), DISTINCT from the per-claim rows above:
# claims stay exactly as they were -- multiple per request AND per wallet,
# a public statement of intent, never exclusive (see StoredClaim below). This
# is a separate, request-level lifecycle value layered on top, so a filer or
# any other agent can answer "where does this actually stand" without
# scanning the claims log themselves.
#
#   pending   -- no claim has ever been made (the default; see get_statuses).
#   claimed   -- at least one claim exists. Set on EVERY successful claim()
#                call, including a repeat one and one that arrives after
#                `completed` -- a new claim always means someone is (still,
#                or again) building this, so it reopens a completed request
#                rather than being blocked by it (owner-style default: an
#                abandoned or lapsed "done" should be reclaimable by the next
#                builder, same as the claim mechanism itself is non-exclusive).
#   completed -- an explicit, PAID, self-declared "this is done" from a
#                wallet that has claimed the request at some point (not
#                necessarily the latest claimer -- see FeatureService.
#                mark_completed). This is NEVER verified: same "evidence, not
#                a guarantee" honesty this codebase already applies to
#                receipts and grading (docs/x402-execution-trust-evaluation.md)
#                -- there is no adjudication of whether the work was actually
#                done, on purpose, to avoid re-deriving the already-rejected
#                escrow-with-an-enforcement-arm pattern.
FEATURE_STATUS_PENDING = "pending"
FEATURE_STATUS_CLAIMED = "claimed"
FEATURE_STATUS_COMPLETED = "completed"


class FeatureError(PlatformError):
    """A feature-board error mapped to an HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        """Map a feature-board error code to its HTTP status via http_status_for_code."""
        super().__init__(code, message, http_status=http_status_for_code(code))


@dataclass
class StoredFeatureRequest:
    """One feature request: what somebody asked for.

    No term_end, unlike StoredPlacement. A board tile is rented advertising
    that must expire; a feature request is a durable statement of demand whose
    whole value is accumulating votes over time. Expiring one would silently
    destroy the demand signal its voters paid to build.

    Filing is free and anonymous, so `submitter` and `settlement_tx_id` are
    empty for requests filed through the HTTP route. Both columns are kept
    so a request created against a settled payment can still be attributed;
    the paid demand surface serves an empty submitter as null, never as a
    made-up value.
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
class StoredClaim:
    """One paid "I'm building this" declaration against a request (migration 098).

    `claimer` is the paying wallet from the settled payment, never anything
    the request body claimed. Multiple claims per request, and per wallet,
    are allowed: a claim is a public statement of intent that the payment
    makes costly, not an exclusive lock on the request -- two builders may
    both be building it, and a builder may re-declare after going quiet.
    This row shape is UNCHANGED by the request-level status added in
    migration 119 (see FEATURE_STATUS_* above) -- status is a derived
    lifecycle value layered on top, not a field on the claim itself.
    """

    request_id: str
    claimer: str
    settlement_tx_id: str
    claimed_at_epoch: int


@dataclass
class ClaimSummary:
    """What both read surfaces show about a request's claims: how many, and who last claimed.

    `count` is over a bounded partition read (see FeatureStore.get_claim_summaries),
    so it saturates at that bound rather than being unbounded.
    """

    count: int = 0
    latest_claimer: str = ""


@dataclass
class RankedFeatureRequest:
    """One request plus its demand total, as served by the PAID demand read."""

    request: StoredFeatureRequest
    vote_total: int
