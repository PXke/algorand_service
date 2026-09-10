"""The bookkeeping ledger every settled x402 payment gets written to.

CLAUDE.md section 9 -- shared across every paid module, not owned by any one
of them. The ledger's table (x402_settlements, migration 090) is generic:
day-bucketed partition, network recorded per row so a TestNet/Mainnet mix is
never summed together.
See require_paid_request in modules/x402/paid_request.py, the one place that
should ever call record_settlement.

Two things every row carries beyond the raw payment:

* `eur_value`: the EUR value at settlement time, from the shared price oracle.
  When no rate is available it is EUR_VALUE_UNAVAILABLE (-1.0), never 0.0 --
  a zero would read as "this settlement was worth nothing" (CLAUDE.md
  section 2, invariant 8).
* `fulfilled`: written False at settlement, flipped to True by mark_fulfilled
  once the route has durably stored what the payer bought (migration 095).
  A row that stays False is a paid-but-unfulfilled settlement an operator
  must reconcile by hand -- see paid_request.py for the contract routes
  follow.
* `refund_tx_id` / `refund_status`: written by record_refund (migration 102)
  when a route opted into paid_request.run_with_refund and its product write
  failed after payment settled. Both stay null on a row that never needed a
  refund attempt -- null refund_status is NOT the same thing as "no refund
  needed": pair it with `fulfilled` to tell them apart (fulfilled=true,
  refund_status=null -> succeeded normally; fulfilled=false,
  refund_status=null -> failed and no refund was ever attempted, an open
  reconciliation item; fulfilled=false, refund_status set -> a refund was
  attempted, check the status).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.x402.assets import asset_for_asa_id
from app.modules.x402.guard import PaymentResult
from app.modules.x402.price_oracle import eur_pricing_id, get_eur_rate
from app.modules.x402.probe_payers import is_probe_payer

logger = logging.getLogger(__name__)

# Sentinel for "no EUR rate was available when this settled". Negative on
# purpose: no real valuation is negative, so an accounting read can filter or
# flag these rows without confusing them with a genuinely tiny payment.
EUR_VALUE_UNAVAILABLE = -1.0


@dataclass
class SettlementRecord:
    """One settled x402 payment, for the bookkeeping ledger."""

    tx_id: str
    asset_id: str
    amount_atomic: str
    payer: str
    resource: str
    network: str
    settled_at_epoch: int
    # EUR value at settlement time, or EUR_VALUE_UNAVAILABLE.
    eur_value: float = EUR_VALUE_UNAVAILABLE
    # False until the route's product write succeeded and mark_fulfilled ran.
    fulfilled: bool = False
    # Set by record_refund (migration 102) once a refund has been attempted
    # for this settlement. Both stay None until then -- see this module's
    # own docstring for how to read them together with `fulfilled`.
    refund_tx_id: str | None = None
    refund_status: str | None = None


class SettlementStore(Protocol):
    """Storage interface for the settlement ledger."""

    def record_settlement(self, item: SettlementRecord) -> None:
        """Append one settled payment to the bookkeeping ledger."""
        ...

    def get_settlement(self, tx_id: str) -> SettlementRecord | None:
        """The ledger row for one settlement txid, or None if it was never recorded."""
        ...

    def mark_fulfilled(self, tx_id: str) -> bool:
        """Flip one settlement to fulfilled. Returns False if no such row exists."""
        ...

    def record_refund(self, tx_id: str, *, refund_tx_id: str | None, refund_status: str) -> bool:
        """Record a refund attempt's outcome for one settlement. Returns False if no such row exists."""
        ...

    def list_for_day(self, day: str, *, limit: int) -> list[SettlementRecord]:
        """One UTC day's settlements (YYYY-MM-DD), newest first, bounded."""
        ...


class CassandraSettlementStore:
    """Cassandra-backed settlement ledger."""

    def record_settlement(self, item: SettlementRecord) -> None:
        """Append one settled payment to the bookkeeping ledger.

        Ledger row first, then the by-txid lookup row: if the second write
        fails, the durable accounting row still exists and only mark_fulfilled
        loses its way to it (which it logs).
        """
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import X402Stmts

        session = get_cassandra_session()
        settled_at = datetime.fromtimestamp(item.settled_at_epoch, tz=UTC)
        day = settled_at.strftime("%Y-%m-%d")
        session.execute(
            X402Stmts.INSERT_SETTLEMENT,
            (
                day,
                settled_at,
                item.tx_id,
                item.asset_id,
                item.amount_atomic,
                item.payer,
                item.resource,
                item.network,
                item.eur_value,
                item.fulfilled,
                item.refund_tx_id,
                item.refund_status,
            ),
        )
        session.execute(
            X402Stmts.INSERT_SETTLEMENT_BY_TX,
            (
                item.tx_id,
                day,
                settled_at,
                item.asset_id,
                item.amount_atomic,
                item.payer,
                item.resource,
                item.network,
                item.eur_value,
                item.fulfilled,
                item.refund_tx_id,
                item.refund_status,
            ),
        )

    def get_settlement(self, tx_id: str) -> SettlementRecord | None:
        """The ledger row for one settlement txid, via the by-txid lookup table."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import X402Stmts

        row = get_cassandra_session().execute(X402Stmts.GET_SETTLEMENT_BY_TX, (tx_id,)).one()
        if row is None:
            return None
        return SettlementRecord(
            tx_id=row.tx_id,
            asset_id=row.asset_id or "",
            amount_atomic=row.amount_atomic or "",
            payer=row.payer or "",
            resource=row.resource or "",
            network=row.network or "",
            settled_at_epoch=int(row.settled_at.replace(tzinfo=UTC).timestamp()),
            eur_value=EUR_VALUE_UNAVAILABLE if row.eur_value is None else float(row.eur_value),
            fulfilled=bool(row.fulfilled),
            refund_tx_id=row.refund_tx_id,
            refund_status=row.refund_status,
        )

    def mark_fulfilled(self, tx_id: str) -> bool:
        """Flip one settlement to fulfilled in both tables.

        Read-then-update on the exact primary key of a row that is known to
        exist: the lookup gives us the (day, settled_at) half of the ledger
        key, and refusing to update without it is what keeps this from
        upserting a phantom row (CLAUDE.md section 3) for a txid the ledger
        never recorded.
        """
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import X402Stmts

        session = get_cassandra_session()
        row = session.execute(X402Stmts.GET_SETTLEMENT_BY_TX, (tx_id,)).one()
        if row is None:
            return False
        session.execute(
            X402Stmts.MARK_SETTLEMENT_FULFILLED,
            (True, row.day, row.settled_at, tx_id),
        )
        session.execute(X402Stmts.MARK_SETTLEMENT_BY_TX_FULFILLED, (True, tx_id))
        return True

    def record_refund(self, tx_id: str, *, refund_tx_id: str | None, refund_status: str) -> bool:
        """Record a refund attempt's outcome in both tables.

        Same read-then-update-on-known-key safety as mark_fulfilled: the
        lookup gives us the (day, settled_at) half of the ledger key, so this
        never upserts a phantom row for a txid the ledger never recorded.
        """
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import X402Stmts

        session = get_cassandra_session()
        row = session.execute(X402Stmts.GET_SETTLEMENT_BY_TX, (tx_id,)).one()
        if row is None:
            return False
        session.execute(
            X402Stmts.MARK_SETTLEMENT_REFUNDED,
            (refund_tx_id, refund_status, row.day, row.settled_at, tx_id),
        )
        session.execute(
            X402Stmts.MARK_SETTLEMENT_BY_TX_REFUNDED, (refund_tx_id, refund_status, tx_id)
        )
        return True

    def list_for_day(self, day: str, *, limit: int) -> list[SettlementRecord]:
        """One UTC day's settlements, newest first, bounded by a single-partition LIMIT read.

        Fixed 2026-09-06 (an adversarial review flagged the wrong premise
        this docstring used to state): x402_settlements is declared `WITH
        CLUSTERING ORDER BY (settled_at DESC, tx_id ASC)` (migration 090),
        NOT the CQL default of ascending -- a bare `SELECT ... LIMIT ?` with
        no ORDER BY already reads in that declared order, i.e. newest first,
        directly from Cassandra. The `.reverse()` this method used to apply
        was written under the false belief that the table defaulted to
        ascending order; against the real schema it silently flipped an
        already-correct newest-first LIMITed page into oldest-first, while
        this docstring's own summary line kept claiming "newest first."
        """
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import X402Stmts

        rows = get_cassandra_session().execute(
            X402Stmts.LIST_SETTLEMENTS_FOR_DAY_FULL, (day, limit)
        )
        return [
            SettlementRecord(
                tx_id=row.tx_id,
                asset_id=row.asset_id or "",
                amount_atomic=row.amount_atomic or "",
                payer=row.payer or "",
                resource=row.resource or "",
                network=row.network or "",
                settled_at_epoch=int(row.settled_at.replace(tzinfo=UTC).timestamp()),
                eur_value=EUR_VALUE_UNAVAILABLE if row.eur_value is None else float(row.eur_value),
                fulfilled=bool(row.fulfilled),
                refund_tx_id=row.refund_tx_id,
                refund_status=row.refund_status,
            )
            for row in rows
        ]


class InMemorySettlementStore:
    """In-memory settlement ledger for dev and tests."""

    def __init__(self) -> None:
        """Start with an empty ledger."""
        self.settlements: list[SettlementRecord] = []

    def record_settlement(self, item: SettlementRecord) -> None:
        """Append one settled payment to the bookkeeping ledger."""
        self.settlements.append(item)

    def get_settlement(self, tx_id: str) -> SettlementRecord | None:
        """The ledger row for one settlement txid, or None."""
        return next((s for s in self.settlements if s.tx_id == tx_id), None)

    def mark_fulfilled(self, tx_id: str) -> bool:
        """Flip one settlement to fulfilled. Returns False if no such row exists."""
        record = self.get_settlement(tx_id)
        if record is None:
            return False
        record.fulfilled = True
        return True

    def record_refund(self, tx_id: str, *, refund_tx_id: str | None, refund_status: str) -> bool:
        """Record a refund attempt's outcome. Returns False if no such row exists."""
        record = self.get_settlement(tx_id)
        if record is None:
            return False
        record.refund_tx_id = refund_tx_id
        record.refund_status = refund_status
        return True

    def list_for_day(self, day: str, *, limit: int) -> list[SettlementRecord]:
        """One UTC day's settlements, newest first, bounded."""
        day_rows = [
            s
            for s in self.settlements
            if datetime.fromtimestamp(s.settled_at_epoch, tz=UTC).strftime("%Y-%m-%d") == day
        ]
        day_rows.sort(key=lambda s: s.settled_at_epoch, reverse=True)
        return day_rows[:limit]


_factory: StoreFactory[SettlementStore] = StoreFactory(
    backend_name=lambda: settings.x402_settlement_store,
    cassandra=CassandraSettlementStore,
    memory=InMemorySettlementStore,
)
# One shared ledger for every paid product, on its own backend setting
# (X402_SETTLEMENT_STORE). falcon_main._register_x402_routes refuses to start
# the paid routes outside dev while this is "memory" -- CLAUDE.md section 9:
# every settlement is logged, durably.


def get_settlement_store() -> SettlementStore:
    """Return the process-wide settlement store, built from settings on first use."""
    return _factory.get()


def set_settlement_store(store: SettlementStore | None) -> None:
    """Override the process-wide settlement store (test seam); None restores lazy build."""
    _factory.set(store)


def eur_value_at_settlement(
    *, asset_id: str | None, amount_atomic: str | None, network: str
) -> float:
    """EUR value of one settled amount right now, or EUR_VALUE_UNAVAILABLE.

    Never raises: a valuation problem is logged and recorded as the sentinel,
    because the payer has already been charged and the ledger write must not
    depend on CoinGecko or Redis being up.
    """
    asset = asset_for_asa_id(asset_id, network)
    if asset is None:
        logger.warning(
            "x402 settlement: asset_id %r on network %s is not an accepted asset; "
            "recording eur_value as unavailable",
            asset_id,
            network,
        )
        return EUR_VALUE_UNAVAILABLE
    try:
        atomic = int(str(amount_atomic or "").strip())
    except ValueError:
        logger.warning(
            "x402 settlement: amount_atomic %r is not an integer; recording eur_value as "
            "unavailable",
            amount_atomic,
        )
        return EUR_VALUE_UNAVAILABLE
    try:
        rate = get_eur_rate(eur_pricing_id(asset))
    except Exception:
        logger.warning(
            "x402 settlement: EUR rate lookup raised for %s; recording eur_value as unavailable",
            asset.symbol,
            exc_info=True,
        )
        return EUR_VALUE_UNAVAILABLE
    if rate is None:
        logger.warning(
            "x402 settlement: no EUR rate for %s; recording eur_value as unavailable",
            asset.symbol,
        )
        return EUR_VALUE_UNAVAILABLE
    units = Decimal(atomic) / (Decimal(10) ** asset.decimals)
    return float(units * rate)


def record_settlement(
    result: PaymentResult,
    *,
    resource: str,
    store: SettlementStore | None = None,
) -> None:
    """Append a settled payment to the bookkeeping ledger, as unfulfilled.

    Never raises. The payer has already been charged by the time this runs, so
    a ledger failure logs at ERROR with every field inline -- the row stays
    recoverable from the log -- and lets the caller serve the paid response.
    """
    network = result.network or settings.x402_network
    record = SettlementRecord(
        tx_id=result.payment_txid or "",
        asset_id=result.asset_id or "",
        amount_atomic=result.amount_atomic or "",
        payer=result.payer or "",
        resource=resource,
        network=network,
        settled_at_epoch=int(datetime.now(tz=UTC).timestamp()),
        eur_value=eur_value_at_settlement(
            asset_id=result.asset_id, amount_atomic=result.amount_atomic, network=network
        ),
        fulfilled=False,
    )
    try:
        (store or get_settlement_store()).record_settlement(record)
    except Exception:
        logger.exception(
            "x402 SETTLEMENT LEDGER WRITE FAILED — payment already settled, response still "
            "served. Recover this row by hand: tx_id=%s asset_id=%s amount_atomic=%s "
            "payer=%s resource=%s network=%s settled_at_epoch=%s eur_value=%s fulfilled=%s",
            record.tx_id,
            record.asset_id,
            record.amount_atomic,
            record.payer,
            record.resource,
            record.network,
            record.settled_at_epoch,
            record.eur_value,
            record.fulfilled,
        )


def mark_fulfilled(
    settlement_tx_id: str | None,
    *,
    resource: str,
    store: SettlementStore | None = None,
) -> bool:
    """Record that the product a settlement paid for has been durably stored.

    Call this AFTER the product write succeeded, never before (store before
    mark, CLAUDE.md section 2 invariant 2). Idempotent: marking twice is a
    no-op. Never raises -- the product write already happened and the response
    must still be served -- but a failure or a missing ledger row is logged at
    ERROR with the txid so the row can be reconciled by hand.

    Returns True when the ledger now says fulfilled, False otherwise.
    """
    if not settlement_tx_id:
        logger.error(
            "x402 mark_fulfilled called with no settlement txid for resource %s; the ledger "
            "row (if any) stays unfulfilled",
            resource,
        )
        return False
    try:
        marked = (store or get_settlement_store()).mark_fulfilled(settlement_tx_id)
    except Exception:
        logger.exception(
            "x402 SETTLEMENT FULFILLMENT MARK FAILED — product stored, ledger still says "
            "unfulfilled. Reconcile by hand: tx_id=%s resource=%s",
            settlement_tx_id,
            resource,
        )
        return False
    if not marked:
        logger.error(
            "x402 mark_fulfilled found no ledger row for tx_id=%s resource=%s; the "
            "settlement write must have failed earlier (see its own ERROR line)",
            settlement_tx_id,
            resource,
        )
    return marked


def record_refund(
    settlement_tx_id: str | None,
    *,
    refund_tx_id: str | None,
    refund_status: str,
    resource: str,
    store: SettlementStore | None = None,
) -> bool:
    """Record a refund attempt's outcome on the settlement ledger.

    Call this AFTER attempting the refund (whatever its outcome), never
    before -- same store-before-mark discipline as mark_fulfilled, just for
    the refund leg instead of the fulfillment leg. Never raises -- the refund
    attempt already happened (or was skipped) and the caller must still
    return a response -- but a failure or a missing ledger row is logged at
    ERROR with the txid so the row can be reconciled by hand.

    Returns True when the ledger now carries the refund outcome, False
    otherwise.
    """
    if not settlement_tx_id:
        logger.error(
            "x402 record_refund called with no settlement txid for resource %s; the refund "
            "outcome (status=%s, refund_tx_id=%s) cannot be attached to any ledger row",
            resource,
            refund_status,
            refund_tx_id,
        )
        return False
    try:
        recorded = (store or get_settlement_store()).record_refund(
            settlement_tx_id, refund_tx_id=refund_tx_id, refund_status=refund_status
        )
    except Exception:
        logger.exception(
            "x402 REFUND OUTCOME RECORD FAILED — refund status=%s refund_tx_id=%s, ledger "
            "still shows no refund attempt. Reconcile by hand: tx_id=%s resource=%s",
            refund_status,
            refund_tx_id,
            settlement_tx_id,
            resource,
        )
        return False
    if not recorded:
        logger.error(
            "x402 record_refund found no ledger row for tx_id=%s resource=%s; the settlement "
            "write must have failed earlier (see its own ERROR line)",
            settlement_tx_id,
            resource,
        )
    return recorded


def recent_real_settlements(
    *, limit: int = 25, lookback_days: int = 7, store: SettlementStore | None = None
) -> list[SettlementRecord]:
    """Real (non-probe) settlements, newest first, bounded.

    "Real" means the payer is not in X402_PROBE_PAYERS -- our own
    self-verification payments are excluded here the same way they are
    excluded from every ranking (modules/x402/probe_payers.py), so this is
    honest proof-of-customer-volume, not our own traffic wearing that label.

    Scans today's UTC day partition backwards up to `lookback_days`, stopping
    once `limit` real settlements have been collected -- bounded on both
    axes, never an unbounded ledger scan (CLAUDE.md section 4).
    """
    active_store = store or get_settlement_store()
    collected: list[SettlementRecord] = []
    day = datetime.now(tz=UTC)
    for _ in range(max(1, lookback_days)):
        for record in active_store.list_for_day(day.strftime("%Y-%m-%d"), limit=200):
            if not is_probe_payer(record.payer):
                collected.append(record)
                if len(collected) >= limit:
                    return collected
        day = day.fromtimestamp(day.timestamp() - 86400, tz=UTC)
    return collected
