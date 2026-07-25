"""Parsing payment details out of a signed transaction's JSON."""

from __future__ import annotations

from app.modules.chain.payment import payment_details_from_txn_json


def test_payment_details_from_signed_txn_json() -> None:
    """Extracts the receiver address and amount from a signed pay-txn JSON blob."""
    wallet = "W" * 58
    treasury = "T" * 58
    txn_json = f'{{"txn":{{"type":"pay","snd":"{wallet}","rcv":"{treasury}","amt":15000}}}}'
    parsed = payment_details_from_txn_json(txn_json)
    assert parsed is not None
    receiver, amount = parsed
    assert receiver.startswith("T")
    assert amount == 15000


def test_payment_details_rejects_non_pay() -> None:
    """Returns None for a non-payment transaction type."""
    assert payment_details_from_txn_json('{"txn":{"type":"appl"}}') is None
