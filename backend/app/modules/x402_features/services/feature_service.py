"""Feature-request rules: request identity, paid voting, demand ranking.

Three product decisions live in this file and are documented at the code that
implements them, because none of them is derivable from the roadmap line
alone:

  * a wallet may vote as many times as it pays -- see `vote`
  * the demand total is a counter, never a read-modify-write -- see `vote`
  * the ranking is an in-memory sort over a bounded scan, not a third
    denormalized table -- see `rank_by_demand`
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

from app.core.config import settings
from app.modules.x402_features.models.domain import (
    FeatureError,
    RankedFeatureRequest,
    StoredFeatureRequest,
    StoredVote,
)
from app.modules.x402_features.stores.base import FeatureStore
from app.modules.x402_features.stores.factory import get_feature_store

logger = logging.getLogger(__name__)

_MAX_TITLE_LENGTH = 120
_MAX_DESCRIPTION_LENGTH = 2000


def request_id_for(*, settlement_tx_id: str) -> str:
    """Identity of one feature request: a hex SHA-256 of the settling payment's txid.

    One payment creates exactly one request, so the payment's own txid is a
    natural unique key, and deriving the id from it means any row in the
    settlement ledger can be traced forward to the request it bought. Replay
    protection in modules/x402/replay.py already stops the same payment header
    being presented twice, so this cannot collide with itself.

    NOT keyed on (submitter, title) the way the board keys placements on
    (payer, link). A board placement is a rented slot the same payer renews;
    a feature request is an event. The same wallet asking for the same thing
    twice has paid twice and stated its demand twice -- folding those onto one
    row would silently delete a paid request.

    When the gate could not give us a txid, a random id stands in rather than a
    constant: falling back to a fixed value would make every unattributable
    payment collide onto one row, so the last such payer would overwrite the
    previous one's paid request. Same failure mode the board's _owner_key
    guards against.
    """
    txid = settlement_tx_id.strip()
    if not txid:
        return uuid.uuid4().hex
    return hashlib.sha256(txid.encode()).hexdigest()


def _clean_title(raw: str) -> str:
    """Validate and trim a request title."""
    title = raw.strip()
    if not title:
        raise FeatureError("invalid_request", "title must not be empty")
    return title[:_MAX_TITLE_LENGTH]


class FeatureService:
    """Creates feature requests, records paid votes, and ranks demand."""

    def __init__(self, store: FeatureStore | None = None) -> None:
        """Take an explicit store for tests; otherwise resolve the configured one lazily."""
        self._store = store

    @property
    def store(self) -> FeatureStore:
        """The injected store, or the process-wide one built from settings."""
        return self._store or get_feature_store()

    def create(
        self,
        *,
        title: str,
        description: str,
        submitter: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> StoredFeatureRequest:
        """Store a paid feature request and return it."""
        moment = now or datetime.now(tz=UTC)
        item = StoredFeatureRequest(
            request_id=request_id_for(settlement_tx_id=settlement_tx_id),
            title=_clean_title(title),
            description=description.strip()[:_MAX_DESCRIPTION_LENGTH],
            submitter=submitter.strip(),
            settlement_tx_id=settlement_tx_id,
            created_at_epoch=int(moment.timestamp()),
        )
        self.store.insert(item)
        return item

    def exists(self, request_id: str) -> bool:
        """Whether a request id refers to a real request.

        Called BEFORE the vote route's payment gate, so a vote for a request
        that does not exist is a 404 rather than a payment taken for an
        increment that can never land. There is no delete path in this module,
        so a request that exists at this check still exists when the payment
        settles.
        """
        return self.store.get(request_id) is not None

    def vote(
        self,
        *,
        request_id: str,
        voter: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> int:
        """Record one paid vote against a request and return the resulting total.

        **A wallet may vote as many times as it pays.** This is a costly-signal
        board, not an election: the demand number IS the amount of money staked
        on a request, and each settled payment adds one unit of it. A
        one-vote-per-wallet cap was considered and rejected -- it would cost a
        sybil nothing to route around (wallets are free to create; only the
        payment is scarce, and the sybil pays the same total either way) while
        costing an honest agent the ability to say "I want this ten times more
        than that". A cap would throw away intensity from the honest and stop
        nobody. See the module docstring for the surface this feeds.

        Because the vote price is flat, the vote COUNT is already amount-
        weighted -- count x price is exactly the USDC staked, which is the
        anti-gaming property CLAUDE.md section 9's ranking guidance asks for.
        If a variable or bid-your-own vote amount is ever introduced, this
        total must switch to summing atomic units paid; a count would then be
        gameable by splitting one big vote into many dust ones.

        Order matters. The increment happens FIRST because it is the thing the
        payer paid for; the audit row is appended after. An audit-append
        failure is logged loudly and does NOT fail the request: the payment
        settled and the vote counted, so turning that into a 5xx would tell the
        payer their paid vote was lost when it was not, and the payment itself
        is still in the shared settlement ledger either way. An increment
        failure, by contrast, is allowed to propagate -- there the payer really
        did get nothing, and it must be loud.

        The returned total is a read-back of the counter and may lag a vote
        that settled concurrently; it is a courtesy echo for the payer, not the
        ranking. The stored total is always exact -- see the store's
        increment_vote_total.
        """
        moment = now or datetime.now(tz=UTC)
        self.store.increment_vote_total(request_id)
        try:
            self.store.append_vote(
                StoredVote(
                    request_id=request_id,
                    voter=voter.strip(),
                    settlement_tx_id=settlement_tx_id,
                    voted_at_epoch=int(moment.timestamp()),
                )
            )
        except Exception:
            logger.warning(
                "x402 feature vote counted but its audit row failed to store "
                "(request_id=%s settlement_tx_id=%s)",
                request_id,
                settlement_tx_id,
                exc_info=True,
            )
        return self.store.get_vote_total(request_id)

    def list_recent(self, *, limit: int) -> list[StoredFeatureRequest]:
        """Return requests newest-first, clamped to the configured maximum.

        Feeds the FREE browse surface, which carries no vote counts -- this
        deliberately does not read any total. Free answers "what has been
        asked for"; paid answers "how much is it wanted".
        """
        clamped = max(1, min(limit, settings.x402_features_max_results))
        return self.store.list_recent(limit=clamped)

    def rank_by_demand(self, *, limit: int) -> list[RankedFeatureRequest]:
        """Return requests ranked by demand total, highest first, clamped.

        Ranked by an in-memory sort over a bounded scan, NOT by a third
        denormalized `by_demand` table. A rank projection clustered on the vote
        total would have to be delete-then-reinserted on every vote, and the
        total it clusters on is a Cassandra counter -- so two concurrent votes
        would interleave their read-old/delete-old/insert-new sequences and
        leave stale or duplicate rank rows behind. The atomic counter is what
        makes voting safe, and it is precisely what a rank projection cannot be
        maintained against. Sorting at read time keeps the counter authoritative
        and has no such race.

        The scan is bounded twice over: the candidate set is the same single
        bounded partition the browse feed reads, and it is LIMITed to
        x402_features_demand_scan_limit. That means the ranking is exact while
        the board fits inside the scan window and degrades to "the top of the
        N most recent" beyond it -- an honest limit, stated here rather than
        discovered later. The fix when the board outgrows it is a rank
        projection rebuilt by a periodic sweep (which can safely read the
        counters at rest), not a live-updated one.

        Ties on vote total break by created_at descending then request_id, so
        the ranking is stable across calls rather than reordering at random
        between two equally-wanted requests.
        """
        clamped = max(1, min(limit, settings.x402_features_max_results))
        candidates = self.store.list_recent(limit=settings.x402_features_demand_scan_limit)
        totals = self.store.get_vote_totals([item.request_id for item in candidates])
        ranked = [
            RankedFeatureRequest(request=item, vote_total=totals.get(item.request_id, 0))
            for item in candidates
        ]
        ranked.sort(
            key=lambda r: (
                -r.vote_total,
                -r.request.created_at_epoch,
                r.request.request_id,
            )
        )
        return ranked[:clamped]
