"""Mapping a raw Cassandra chain row to an IndexedTransaction."""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.chain.repository import row_to_indexed_transaction


def test_row_to_indexed_transaction_maps_fields() -> None:
    """Maps every raw Cassandra row field onto the IndexedTransaction dataclass."""
    row = SimpleNamespace(
        txid="T" * 52,
        round=99,
        intra=3,
        sender="S" * 58,
        txn_type="appl",
        txn_json='{"type":"appl"}',
    )
    tx = row_to_indexed_transaction(row)

    assert tx is not None
    assert tx.round == 99
    assert tx.intra == 3
    assert tx.sender == "S" * 58
    assert tx.txn_type == "appl"
    assert tx.txn_json == '{"type":"appl"}'


def test_row_to_indexed_transaction_none() -> None:
    """Returns None when given a None row."""
    assert row_to_indexed_transaction(None) is None
