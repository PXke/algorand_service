from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndexedTransaction:
    """Row from Conduit `transactions_by_id`."""

    txid: str
    round: int
    intra: int
    sender: str
    txn_type: str
    txn_json: str | None = None
    receiver: str | None = None
    amount_microalgos: int | None = None
