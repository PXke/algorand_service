"""How much a grader's opinion counts: their all-time spend with this marketplace.

Replaces the previous build's proof-of-payment check, which asked "did this
wallet pay the endpoint it is grading". That question is not answerable from
our ledger and never will be: a payment to somebody else's x402 endpoint goes
to their payTo through their facilitator and never traverses our gate, so it is
not in x402_settlements by construction. The product does not need it answered.

The question this asks instead is one our ledger CAN answer exactly: **how much
has this wallet paid US, across every product in this marketplace.** That is a
costly, ledger-backed, sybil-resistant signal -- a wallet cannot fake spend it
did not make, and splitting one wallet's spend across ten wallets divides its
weight rather than multiplying it -- and it is a reputation number, not a fund
movement. Nothing here holds, escrows, refunds or forfeits anything; it is a
sum computed at read time over rows that were written when payments settled.

## What is summed, and what is deliberately not

Only settlements on the CURRENTLY CONFIGURED network are counted. TestNet USDC
comes free from a public dispenser, so summing TestNet spend as credibility on
MainNet would make the whole weight free to forge. modules/x402/settlement.py
records the network per row precisely so the two are never summed together;
this honours that. The consequence -- flipping x402_network resets every
wallet's weight to the base -- is correct, not a bug: MainNet credibility
should not be inherited from free money.

Atomic units are summed across whatever assets appear on that network. That is
exact while one payment asset is enabled (USDC today), and becomes wrong the
day a second asset with different decimals is enabled, because atomic units of
two assets are not commensurate. See "Observed, not fixed": normalizing per
asset needs a decimals source this module does not have.

## Reading the shared ledger

x402_settlements (migration 090) is partitioned by UTC day with payer as a
plain column, so there is no key-addressable "what has this payer spent". The
options are a filtered read -- forbidden, CLAUDE.md section 4 bars ALLOW
FILTERING on non-key columns -- or reading whole day partitions by key and
summing in Python, which is what this does.

The scan is bounded twice: at most `x402_grading_spend_lookback_days`
partition reads, each with a bound LIMIT of `x402_grading_spend_scan_limit`
rows. It also answers for ALL of an aggregate's graders in ONE pass, so a paid
score lookup costs a fixed number of queries no matter how many wallets graded
the URL -- a per-payer lookup would have multiplied the day scans by the grader
count on a request path.

The right long-run shape is still a by-payer projection of the ledger,
dual-written where record_settlement writes the canonical row. That lives in
modules/x402/settlement.py, which this change is not authorized to modify, so
it is flagged rather than done here.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.core.cassandra import get_cassandra_session
from app.core.config import settings
from app.core.statements import X402GradingStmts
from app.core.store_factory import StoreFactory
from app.modules.x402.settlement import get_settlement_store

logger = logging.getLogger(__name__)


def _atomic(raw: str | None) -> int:
    """Parse one settlement's amount_atomic, treating an unparseable one as zero.

    amount_atomic is a text column fed by the facilitator's response, so a
    non-numeric value is possible and must not take down a paid read. It is
    logged and skipped: under-counting one row's contribution to a reputation
    weight is a far smaller failure than a 500 on a request the caller paid
    for.
    """
    try:
        return max(0, int(str(raw or "0").strip() or "0"))
    except ValueError:
        logger.warning("x402 grading credibility: unparseable settlement amount_atomic %r", raw)
        return 0


class SpendLookup(Protocol):
    """Read side of the shared settlement ledger, for credibility weights only."""

    def spend_by_payer(self, payers: Sequence[str]) -> dict[str, int] | None:
        """Total atomic spend with this marketplace, per payer, for the payers asked about.

        Returns a mapping that contains every requested payer (0 for one with
        no settlements found), or None -- never an empty mapping, never zeros
        -- when the ledger could not be read. "Could not determine" and
        "definitely never spent" must stay distinguishable by the caller
        (CLAUDE.md section 2 invariant 8: an error result must never be
        readable as ground truth), because the two produce different aggregates
        and the paid response says which one happened.
        """
        ...


class InMemorySpendLookup:
    """Sums the shared in-memory settlement ledger, for dev and tests.

    Deliberately reads modules/x402's own InMemorySettlementStore instance
    rather than keeping a copy: dev and tests must see exactly the payments
    require_paid_request recorded, not a parallel list that can drift from it.
    """

    def spend_by_payer(self, payers: Sequence[str]) -> dict[str, int] | None:
        """Sum the in-memory ledger's atomic amounts per payer, current network only."""
        store = get_settlement_store()
        settlements = getattr(store, "settlements", None)
        if settlements is None:
            # The configured ledger backend is not the in-memory one, so this
            # lookup is pointed at the wrong store. Undeterminable, not "zero".
            logger.warning(
                "x402 grading credibility: in-memory lookup got a %s settlement store",
                type(store).__name__,
            )
            return None
        wanted = set(payers)
        totals = dict.fromkeys(wanted, 0)
        for record in settlements:
            if record.payer in wanted and record.network == settings.x402_network:
                totals[record.payer] += _atomic(record.amount_atomic)
        return totals


class CassandraSpendLookup:
    """Sums the shared ledger's recent day partitions, bounded by days x rows.

    See the module docstring for why this reads whole day partitions, why it is
    bounded twice, and why it answers for every payer in one pass.
    """

    def __init__(self, session_provider: object | None = None) -> None:
        """Take an explicit Cassandra session provider for tests; default to the shared one."""
        self._session_provider = session_provider or get_cassandra_session

    def spend_by_payer(self, payers: Sequence[str]) -> dict[str, int] | None:
        """Sum the ledger's recent day partitions per payer, current network only.

        Sequential by day on purpose, not yet parallelized: a performance
        review flagged this loop as ~lookback_days sequential round trips
        and suggested the shared `execute_parallel_with_args` helper (used
        the same way in x402_features for per-request vote totals) -- but
        that helper always uses the real process-wide session
        (`get_cassandra_session()`), not this class's injected
        `_session_provider`, so switching to it would silently stop
        honoring the test seam this class was built around (including the
        failure-simulation test). The driver's own `execute_concurrent_with_args`
        does accept an explicit session, but this codebase's fake test
        session only implements synchronous `execute()`, not the
        `execute_async()` the concurrent path needs -- fixing this properly
        means either extending that fake session or picking a different
        concurrency approach, a real decision left for a follow-up rather
        than guessed at here. Latency only, not correctness: still bounded,
        still answers for every payer in one pass.
        """
        wanted = set(payers)
        totals = dict.fromkeys(wanted, 0)
        if not wanted:
            return totals
        lookback = max(1, settings.x402_grading_spend_lookback_days)
        scan_limit = max(1, settings.x402_grading_spend_scan_limit)
        today = datetime.now(tz=UTC).date()
        try:
            session = self._session_provider()
            for offset in range(lookback):
                day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
                rows = session.execute(X402GradingStmts.LIST_SETTLEMENTS_FOR_DAY, (day, scan_limit))
                for row in rows:
                    if row.payer in wanted and row.network == settings.x402_network:
                        totals[row.payer] += _atomic(row.amount_atomic)
        except Exception:
            # Undeterminable, not "never spent" -- see SpendLookup.spend_by_payer.
            logger.exception(
                "x402 grading credibility: settlement ledger read failed for %s payer(s)",
                len(wanted),
            )
            return None
        return totals


_factory: StoreFactory[SpendLookup] = StoreFactory(
    # The same setting the shared ledger itself is built from: this reads that
    # ledger, so it must always be pointed at the backend the ledger is on. A
    # separate setting could be set inconsistently and would silently sum an
    # empty store into "nobody has ever paid us". (The name is the accepted
    # pre-existing leak noted in modules/x402/settlement.py -- it is generic
    # now, not directory-specific.)
    backend_name=lambda: settings.x402_directory_store,
    cassandra=CassandraSpendLookup,
    memory=InMemorySpendLookup,
)


def get_spend_lookup() -> SpendLookup:
    """Return the process-wide spend lookup, built from settings on first use."""
    return _factory.get()


def set_spend_lookup(lookup: SpendLookup | None) -> None:
    """Override the process-wide spend lookup (test seam); None restores lazy build."""
    _factory.set(lookup)


def weight_for(spend_atomic: int) -> int:
    """Credibility weight for a wallet that has spent `spend_atomic` with this marketplace.

    `base + spend`, clamped to a maximum. Two decisions worth stating:

    **The base is added, not max()'d, and it is never zero.** Every grade was
    itself paid for, so a grader with no other history has still paid us
    something real and their opinion has to count for more than nothing. A zero
    weight would also silently delete that grade from the weighted mean, which
    is the same failure class as discarding paid work: the wallet paid the
    grading fee and got no representation for it. Adding a floor rather than
    taking max(base, spend) keeps the weight strictly monotonic in spend -- a
    wallet that spends more always outweighs one that spent less, with no flat
    region near the bottom where extra spend buys nothing.

    A useful side effect: when the ledger cannot be read every spend reads as
    0, every weight collapses to `base`, and the weighted mean therefore equals
    the plain mean instead of becoming 0/0. The degraded answer is the honest
    unweighted one (and the response says so via `weights_resolved`), never a
    NaN and never a silent zero-weight wipeout.

    **The clamp exists so credibility cannot simply be bought outright.** With
    an unbounded linear weight, one wallet that has spent enough with us
    outvotes every honest grader combined, which turns "how much they paid us"
    from a reputation signal into a purchase price for any endpoint's score.
    The cap bounds one wallet's influence at a large but finite multiple of a
    newcomer's. It is a config knob rather than a curve (log/sqrt) because a
    clamp is exactly explainable to the agent paying for the number, and the
    serving response includes each grade's weight so the arithmetic is
    checkable.
    """
    base = max(1, settings.x402_grading_base_weight_atomic)
    cap = max(base, settings.x402_grading_max_weight_atomic)
    return min(base + max(0, spend_atomic), cap)
