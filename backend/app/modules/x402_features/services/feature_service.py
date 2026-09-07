"""Feature-request rules: request identity, free filing, paid voting, demand ranking.

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
from app.modules.x402.probe_payers import is_probe_payer
from app.modules.x402_features.models.domain import (
    FEATURE_STATUS_CLAIMED,
    FEATURE_STATUS_COMPLETED,
    ClaimSummary,
    FeatureError,
    RankedFeatureRequest,
    StoredClaim,
    StoredFeatureRequest,
    StoredVote,
)
from app.modules.x402_features.stores.base import FeatureStore
from app.modules.x402_features.stores.factory import get_feature_store

logger = logging.getLogger(__name__)

_MAX_TITLE_LENGTH = 120
_MAX_DESCRIPTION_LENGTH = 2000


def request_id_for(*, settlement_tx_id: str = "") -> str:
    """Identity of one feature request.

    Filing is free and anonymous, so there is normally no settlement to key
    on and the id is a random uuid: each filing is its own event, and two
    identical titles are two requests, never folded onto one row. NOT keyed on
    (submitter, title) the way the board keys placements on (payer, link) --
    a placement is a rented slot the same payer renews; a request is a
    statement of demand and restating it is a second statement.

    When a settlement txid IS supplied (a request created by a paid path), the
    id is the hex SHA-256 of that txid so the settlement ledger row can be
    traced forward to the request it bought.
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
    """Creates (free, anonymous) feature requests, records paid votes, and ranks demand."""

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
        submitter: str = "",
        settlement_tx_id: str = "",
        now: datetime | None = None,
    ) -> StoredFeatureRequest:
        """Store one feature request and return it.

        The HTTP route files anonymously (no submitter, no settlement); the
        two attribution fields exist for callers that do have a settled
        payment to attach.
        """
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
        increment that can never land. Only an admin delete (see `delete`)
        can remove a request between this check and settlement.
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

        A vote paid by one of OUR wallets (x402_probe_payers) is charged and
        audit-logged like any other but is NOT counted: the demand total is a
        counter, so it cannot be filtered at read time, and probe traffic
        must never move a ranking (CLAUDE.md section 9). Skipping the
        increment at write time is the only place that exclusion can live.
        """
        moment = now or datetime.now(tz=UTC)
        if is_probe_payer(voter):
            logger.info(
                "x402 features: vote on %s by probe payer not counted (settlement_tx_id=%s)",
                request_id,
                settlement_tx_id,
            )
        else:
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

    def claim(
        self,
        *,
        request_id: str,
        claimer: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> ClaimSummary:
        """Record one paid "I'm building this" claim and return the request's claim summary.

        Multiple claims are allowed, from different wallets and from the
        same one: the payment makes the declaration costly, which is the
        whole signal, and an exclusive lock would let anyone pay once to
        squat a request nobody else may then declare for. The claim is stored
        under the settled payer -- an unattributable payment is stored with
        an empty claimer rather than dropped, because the payment happened
        and the row is the record that it bought something.

        The returned summary is a read-back and may lag a claim that settled
        concurrently; it is a courtesy echo for the payer.

        Store before mark (CLAUDE.md section 2): the claim row is appended
        FIRST, then the request's lifecycle status is set to 'claimed' --
        unconditionally, even if it was already 'claimed' (a no-op
        transition) or 'completed' (a REOPEN, see domain.FEATURE_STATUS_*
        for why a new claim reopening a completed request is the right
        default). A status-write failure is not caught here: unlike the vote
        audit row, the status IS a product surface both read routes serve,
        so losing it silently would misreport the request as still pending
        after a real, paid claim -- it should propagate the same way
        append_claim's own failure would.
        """
        moment = now or datetime.now(tz=UTC)
        self.store.append_claim(
            StoredClaim(
                request_id=request_id,
                claimer=claimer.strip(),
                settlement_tx_id=settlement_tx_id,
                claimed_at_epoch=int(moment.timestamp()),
            )
        )
        self.store.update_status(request_id, FEATURE_STATUS_CLAIMED)
        return self.store.get_claim_summaries([request_id]).get(request_id, ClaimSummary())

    def mark_completed(self, *, request_id: str, claimer: str) -> None:
        """Explicitly, self-declare a request completed. Never verified.

        Authorization: the settled payer must have claimed this request at
        SOME point -- any past claimer, not only the latest one. That is the
        most defensible bar this module's own trust model supports: a claim
        is already an unverified, costly, public declaration of intent (see
        StoredClaim), so completion is just a further one of the same kind,
        made by someone who at least once put money behind "I am building
        this." This method does NOT verify that the work was actually done
        -- doing so would re-derive the escrow-with-an-enforcement-arm
        pattern already rejected for this marketplace (see
        docs/x402-execution-trust-evaluation.md): the remedy for a false
        completion claim is the same as for any other self-declared
        statement here (reputation via grading), never an adjudication arm.

        Raises FeatureError('request_not_claimed_by_payer') if the payer
        never claimed. This can only be checked AFTER settlement -- there is
        no self-declared wallet field before payment, the same reason the
        claim/vote routes take their identity from the settled payer rather
        than the request body -- so unlike the existence check (checked
        before the payment gate) this rejection happens with money already
        collected. It is intentionally NOT refunded: run_with_refund treats
        any PlatformError raised from a product write as a payment-kept,
        ownership-style rejection (the same shape as the directory's
        relist-not-yours check), not a delivery failure of ours, and this is
        exactly that -- the payer chose to pay for an action they were not
        entitled to take, not a failure on our end.

        A completed request is not terminal: see `claim` for how a later
        claim reopens it back to 'claimed'.
        """
        if not self.store.has_claimed(request_id, claimer.strip()):
            raise FeatureError(
                "request_not_claimed_by_payer",
                "Only a wallet that has claimed this request may mark it completed",
            )
        self.store.update_status(request_id, FEATURE_STATUS_COMPLETED)

    def statuses_for(self, items: list[StoredFeatureRequest]) -> dict[str, str]:
        """Lifecycle statuses for a page of requests, keyed by request id (missing = pending).

        One batched, bounded read, the same shape claim_summaries and the
        vote-total read already use. An unreadable status column degrades to
        "pending" with a log line rather than taking a read surface down --
        the requests are the product and status is an annotation on them,
        same posture claim_summaries already takes.
        """
        if not items:
            return {}
        try:
            return self.store.get_statuses([item.request_id for item in items])
        except Exception:
            logger.warning(
                "x402 features: statuses unreadable; page served as pending",
                exc_info=True,
            )
            return {}

    def claim_summaries(self, items: list[StoredFeatureRequest]) -> dict[str, ClaimSummary]:
        """Claim summaries for a page of requests, keyed by request id (missing = none).

        One batched, bounded read (see FeatureStore.get_claim_summaries). An
        unreadable claims table degrades to "no claims shown" with a log
        line rather than taking the free browse down: the requests are the
        product on that surface and the claims are an annotation.
        """
        if not items:
            return {}
        try:
            return self.store.get_claim_summaries([item.request_id for item in items])
        except Exception:
            logger.warning(
                "x402 features: claim summaries unreadable; page served without them",
                exc_info=True,
            )
            return {}

    def delete(self, request_id: str) -> bool:
        """Admin-only: remove a request, its recency row and its claims outright.

        Returns False if there was nothing to delete, so the admin route can
        tell a real removal from a no-op. Vote counter and audit log stay
        (see the store Protocol). Note `exists()`'s docstring: this is the
        one path that can remove a request between a vote's pre-gate
        existence check and its settlement -- the vote's increment still
        lands on the orphaned counter, which is harmless (nothing reads it).
        """
        return bool(request_id) and self.store.delete(request_id)

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
