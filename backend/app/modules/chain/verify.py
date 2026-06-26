from __future__ import annotations

from app.modules.chain.models import IndexedTransaction
from app.modules.chain.payment import payment_details_from_txn_json

DEFAULT_SUBMISSION_TXN_TYPES = frozenset({"pay", "appl", "axfer"})
SUGGESTION_TXN_TYPES = frozenset({"pay"})


def verify_on_chain_submission(
    tx: IndexedTransaction,
    *,
    wallet_address: str,
    allowed_txn_types: frozenset[str] | None = None,
) -> bool:
    """Confirm an indexed transaction is a valid suggestion submission proof."""
    if tx.sender != wallet_address:
        return False
    allowed = allowed_txn_types if allowed_txn_types is not None else DEFAULT_SUBMISSION_TXN_TYPES
    normalized = tx.txn_type.lower().strip()
    return normalized in allowed


def _payment_receiver_amount(tx: IndexedTransaction) -> tuple[str, int] | None:
    if tx.receiver and tx.amount_microalgos is not None:
        return tx.receiver, tx.amount_microalgos
    return payment_details_from_txn_json(tx.txn_json)


def verify_suggestion_submission(
    tx: IndexedTransaction,
    *,
    wallet_address: str,
    treasury_address: str,
    min_microalgos: int,
) -> bool:
    """Pay txn from session wallet to platform treasury with at least min_microalgos."""
    if not treasury_address:
        return False
    if not verify_on_chain_submission(
        tx,
        wallet_address=wallet_address,
        allowed_txn_types=SUGGESTION_TXN_TYPES,
    ):
        return False
    payment = _payment_receiver_amount(tx)
    if payment is None:
        return False
    receiver, amount = payment
    if receiver != treasury_address:
        return False
    return amount >= min_microalgos
