"""Treasury-payment verification for service suggestions."""

from __future__ import annotations

from app.modules.chain.models import IndexedTransaction
from app.modules.chain.verify import verify_suggestion_submission

TREASURY = "T" * 58
WALLET = "W" * 58


def test_verify_suggestion_submission_accepts_treasury_pay() -> None:
    """Accepts a pay transaction from the wallet to the treasury meeting the minimum amount."""
    tx = IndexedTransaction(
        txid="X" * 52,
        round=1,
        intra=0,
        sender=WALLET,
        txn_type="pay",
        receiver=TREASURY,
        amount_microalgos=10_000,
    )
    assert verify_suggestion_submission(
        tx,
        wallet_address=WALLET,
        treasury_address=TREASURY,
        min_microalgos=10_000,
    )


def test_verify_suggestion_submission_rejects_low_amount() -> None:
    """Rejects a payment below the configured minimum amount."""
    tx = IndexedTransaction(
        txid="X" * 52,
        round=1,
        intra=0,
        sender=WALLET,
        txn_type="pay",
        receiver=TREASURY,
        amount_microalgos=100,
    )
    assert not verify_suggestion_submission(
        tx,
        wallet_address=WALLET,
        treasury_address=TREASURY,
        min_microalgos=10_000,
    )


def test_verify_suggestion_submission_rejects_wrong_treasury() -> None:
    """Rejects a payment sent to a receiver other than the treasury address."""
    tx = IndexedTransaction(
        txid="X" * 52,
        round=1,
        intra=0,
        sender=WALLET,
        txn_type="pay",
        receiver="R" * 58,
        amount_microalgos=10_000,
    )
    assert not verify_suggestion_submission(
        tx,
        wallet_address=WALLET,
        treasury_address=TREASURY,
        min_microalgos=10_000,
    )


def test_verify_suggestion_submission_rejects_wrong_sender() -> None:
    """Rejects a payment whose sender doesn't match the claimed wallet address."""
    tx = IndexedTransaction(
        txid="X" * 52,
        round=1,
        intra=0,
        sender="X" * 58,
        txn_type="pay",
        receiver=TREASURY,
        amount_microalgos=10_000,
    )
    assert not verify_suggestion_submission(
        tx,
        wallet_address=WALLET,
        treasury_address=TREASURY,
        min_microalgos=10_000,
    )


def test_verify_suggestion_submission_rejects_non_pay_txn() -> None:
    """Rejects a non-pay (e.g. application-call) transaction even with matching amount and parties."""
    tx = IndexedTransaction(
        txid="X" * 52,
        round=1,
        intra=0,
        sender=WALLET,
        txn_type="appl",
        receiver=TREASURY,
        amount_microalgos=10_000,
    )
    assert not verify_suggestion_submission(
        tx,
        wallet_address=WALLET,
        treasury_address=TREASURY,
        min_microalgos=10_000,
    )


def test_verify_suggestion_submission_parses_txn_json() -> None:
    """Falls back to parsing the raw txn_json blob when the row lacks structured receiver/amount fields."""
    txn_json = '{"txn":{"type":"pay","snd":"' + WALLET + '","rcv":"' + TREASURY + '","amt":20000}}'
    tx = IndexedTransaction(
        txid="X" * 52,
        round=1,
        intra=0,
        sender=WALLET,
        txn_type="pay",
        txn_json=txn_json,
    )
    assert verify_suggestion_submission(
        tx,
        wallet_address=WALLET,
        treasury_address=TREASURY,
        min_microalgos=10_000,
    )
