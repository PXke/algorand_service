"""Storage for signed fulfillment receipts (migration 109).

Mirrors modules/x402/settlement.py's own shape deliberately (Protocol +
dataclass + Cassandra/memory stores + a StoreFactory, all in one file): this
is comparably small, single-table, no projections, and shares no code with
the ledger it must stay physically separate from -- see receipts.py and the
module docstring on the migration for why "separate from the settlement
ledger" is load-bearing, not a style choice.

A DEDICATED table, NEVER x402_settlements/x402_settlements_by_tx -- the
settlement ledger is transaction metadata only, permanent, per CLAUDE.md
section 9. This store only ever writes/reads x402_fulfillment_receipts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.core.config import settings
from app.core.store_factory import StoreFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReceiptRecord:
    """One signed fulfillment receipt."""

    receipt_id: str
    settlement_tx_id: str
    resource: str
    signing_address: str
    signature: str  # base64, algosdk.util.sign_bytes output
    request_hash: str  # hex sha256 of the raw request body
    response_hash: str  # hex sha256 of the FULL response body, never truncated
    output: str  # the response body text, capped to x402_receipt_output_max_chars
    output_truncated: bool
    issued_at_epoch: int


class ReceiptStore(Protocol):
    """Storage interface for signed fulfillment receipts."""

    def record_receipt(self, item: ReceiptRecord) -> None:
        """Store one receipt."""
        ...

    def get_receipt(self, receipt_id: str) -> ReceiptRecord | None:
        """One receipt by id, or None if it was never recorded or has expired (TTL)."""
        ...


class CassandraReceiptStore:
    """Cassandra-backed receipt storage, TTL'd via the table's own default_time_to_live."""

    def record_receipt(self, item: ReceiptRecord) -> None:
        """Insert one receipt row."""
        import uuid as uuid_module

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import X402ReceiptStmts

        session = get_cassandra_session()
        session.execute(
            X402ReceiptStmts.INSERT_RECEIPT,
            (
                uuid_module.UUID(item.receipt_id),
                item.settlement_tx_id,
                item.resource,
                item.signing_address,
                item.signature,
                item.request_hash,
                item.response_hash,
                item.output,
                item.output_truncated,
                datetime.fromtimestamp(item.issued_at_epoch, tz=UTC),
                item.issued_at_epoch,
            ),
        )

    def get_receipt(self, receipt_id: str) -> ReceiptRecord | None:
        """Point read by receipt_id. None for an unknown id, a malformed (non-UUID) id, or one the table's TTL has expired."""
        import uuid as uuid_module

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import X402ReceiptStmts

        try:
            receipt_uuid = uuid_module.UUID(receipt_id)
        except (ValueError, AttributeError, TypeError):
            return None

        row = get_cassandra_session().execute(X402ReceiptStmts.GET_RECEIPT, (receipt_uuid,)).one()
        if row is None:
            return None
        return ReceiptRecord(
            receipt_id=str(row.receipt_id),
            settlement_tx_id=row.settlement_tx_id or "",
            resource=row.resource or "",
            signing_address=row.signing_address or "",
            signature=row.signature or "",
            request_hash=row.request_hash or "",
            response_hash=row.response_hash or "",
            output=row.output or "",
            output_truncated=bool(row.output_truncated),
            issued_at_epoch=int(row.issued_at_epoch or 0),
        )


class InMemoryReceiptStore:
    """In-memory receipt storage for dev and tests. No TTL enforcement -- see get_receipt's own note."""

    def __init__(self) -> None:
        """Start with an empty store."""
        self.receipts: dict[str, ReceiptRecord] = {}

    def record_receipt(self, item: ReceiptRecord) -> None:
        """Store one receipt, keyed by its id."""
        self.receipts[item.receipt_id] = item

    def get_receipt(self, receipt_id: str) -> ReceiptRecord | None:
        """One receipt by id, or None. Unlike Cassandra this never expires on its own -- fine for dev/test, never used in prod (the store gate in falcon_main.py keeps a "memory" backend's receipt read route unregistered there)."""
        return self.receipts.get(receipt_id)


_factory: StoreFactory[ReceiptStore] = StoreFactory(
    backend_name=lambda: settings.x402_receipts_store,
    cassandra=CassandraReceiptStore,
    memory=InMemoryReceiptStore,
)


def get_receipt_store() -> ReceiptStore:
    """Return the process-wide receipt store, built from settings on first use."""
    return _factory.get()


def set_receipt_store(store: ReceiptStore | None) -> None:
    """Override the process-wide receipt store (test seam); None restores lazy build."""
    _factory.set(store)
