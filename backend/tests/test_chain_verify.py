"""On-chain submission verification accepts/rejects the right transactions."""

from __future__ import annotations

from app.modules.chain.models import IndexedTransaction
from app.modules.chain.verify import verify_on_chain_submission


def test_verify_accepts_matching_pay_tx() -> None:
    """Accepts a pay transaction whose sender matches the claimed wallet."""
    wallet = "W" * 58
    tx = IndexedTransaction(
        txid="T" * 52,
        round=1,
        intra=0,
        sender=wallet,
        txn_type="pay",
    )
    assert verify_on_chain_submission(tx, wallet_address=wallet) is True


def test_verify_rejects_wrong_sender() -> None:
    """Rejects a transaction whose sender differs from the claimed wallet."""
    tx = IndexedTransaction(
        txid="T" * 52,
        round=1,
        intra=0,
        sender="A" * 58,
        txn_type="pay",
    )
    assert verify_on_chain_submission(tx, wallet_address="B" * 58) is False


def test_verify_rejects_disallowed_txn_type() -> None:
    """Rejects a matching-sender transaction whose type isn't in the default allowlist."""
    wallet = "W" * 58
    tx = IndexedTransaction(
        txid="T" * 52,
        round=1,
        intra=0,
        sender=wallet,
        txn_type="keyreg",
    )
    assert verify_on_chain_submission(tx, wallet_address=wallet) is False


def test_verify_allows_custom_txn_types() -> None:
    """Accepts a normally-disallowed txn type when explicitly passed in allowed_txn_types."""
    wallet = "W" * 58
    tx = IndexedTransaction(
        txid="T" * 52,
        round=1,
        intra=0,
        sender=wallet,
        txn_type="keyreg",
    )
    assert (
        verify_on_chain_submission(
            tx,
            wallet_address=wallet,
            allowed_txn_types=frozenset({"keyreg"}),
        )
        is True
    )
