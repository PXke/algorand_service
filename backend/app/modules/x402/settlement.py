"""The bookkeeping ledger every settled x402 payment gets written to.

CLAUDE.md section 9 -- shared across every paid module, not owned by any one
of them. Moved here from x402_directory 2026-08-30: it was the first paid
module built, but the ledger's own table (x402_settlements, migration 090)
was already generic -- no listing-specific field, day-bucketed partition,
network recorded per row so a TestNet/Mainnet mix is never summed together.
See require_paid_request in modules/x402/paid_request.py, the one place that
should ever call record_settlement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.core.config import settings
from app.core.store_factory import StoreFactory
from app.modules.x402.guard import PaymentResult

logger = logging.getLogger(__name__)


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
    # EUR value at settlement time. Always 0.0 today: no FX lookup is built, and
    # a visible zero is preferable to an absent column that later reads as
    # "this settlement had no value".
    eur_value: float = 0.0


class SettlementStore(Protocol):
    """Storage interface for the settlement ledger."""

    def record_settlement(self, item: SettlementRecord) -> None:
        """Append one settled payment to the bookkeeping ledger."""
        ...


class CassandraSettlementStore:
    """Cassandra-backed settlement ledger."""

    def record_settlement(self, item: SettlementRecord) -> None:
        """Append one settled payment to the bookkeeping ledger."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import X402Stmts

        session = get_cassandra_session()
        settled_at = datetime.fromtimestamp(item.settled_at_epoch, tz=UTC)
        session.execute(
            X402Stmts.INSERT_SETTLEMENT,
            (
                settled_at.strftime("%Y-%m-%d"),
                settled_at,
                item.tx_id,
                item.asset_id,
                item.amount_atomic,
                item.payer,
                item.resource,
                item.network,
                item.eur_value,
            ),
        )


class InMemorySettlementStore:
    """In-memory settlement ledger for dev and tests."""

    def __init__(self) -> None:
        """Start with an empty ledger."""
        self.settlements: list[SettlementRecord] = []

    def record_settlement(self, item: SettlementRecord) -> None:
        """Append one settled payment to the bookkeeping ledger."""
        self.settlements.append(item)


_factory: StoreFactory[SettlementStore] = StoreFactory(
    backend_name=lambda: settings.x402_directory_store,
    cassandra=CassandraSettlementStore,
    memory=InMemorySettlementStore,
)
# Reuses x402_directory_store rather than a new x402_settlement_store setting:
# one shared ledger, one shared backend choice. If a future module needs the
# ledger on Cassandra while listings stay on memory (or vice versa), split
# this into its own setting then -- not speculatively now.


def get_settlement_store() -> SettlementStore:
    """Return the process-wide settlement store, built from settings on first use."""
    return _factory.get()


def set_settlement_store(store: SettlementStore | None) -> None:
    """Override the process-wide settlement store (test seam); None restores lazy build."""
    _factory.set(store)


def record_settlement(
    result: PaymentResult,
    *,
    resource: str,
    store: SettlementStore | None = None,
) -> None:
    """Append a settled payment to the bookkeeping ledger.

    Never raises. The payer has already been charged by the time this runs, so
    a ledger failure logs at ERROR with every field inline -- the row stays
    recoverable from the log -- and lets the caller serve the paid response.
    """
    record = SettlementRecord(
        tx_id=result.payment_txid or "",
        asset_id=result.asset_id or "",
        amount_atomic=result.amount_atomic or "",
        payer=result.payer or "",
        resource=resource,
        network=result.network or settings.x402_network,
        settled_at_epoch=int(datetime.now(tz=UTC).timestamp()),
        eur_value=0.0,
    )
    try:
        (store or get_settlement_store()).record_settlement(record)
    except Exception:
        logger.exception(
            "x402 SETTLEMENT LEDGER WRITE FAILED — payment already settled, response still "
            "served. Recover this row by hand: tx_id=%s asset_id=%s amount_atomic=%s "
            "payer=%s resource=%s network=%s settled_at_epoch=%s eur_value=%s",
            record.tx_id,
            record.asset_id,
            record.amount_atomic,
            record.payer,
            record.resource,
            record.network,
            record.settled_at_epoch,
            record.eur_value,
        )
