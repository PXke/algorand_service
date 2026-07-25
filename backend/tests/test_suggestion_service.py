"""Creating a suggestion requires an indexed, correctly-addressed treasury payment."""

from __future__ import annotations

import pytest

from app.modules.chain.models import IndexedTransaction
from app.modules.suggestions.models.domain import SuggestionError
from app.modules.suggestions.models.schemas import CreateSuggestionRequest
from app.modules.suggestions.services.suggestion_service import SuggestionService
from app.modules.suggestions.stores.memory import InMemorySuggestionStore

TREASURY = "T" * 58


class StubChainRepo:
    """Stub ChainRepository returning a fixed transaction (or None) for every lookup."""
    def __init__(self, tx: IndexedTransaction | None) -> None:
        """Return `tx` (or None) for every get_transaction lookup."""
        self._tx = tx

    def get_transaction(self, txid: str) -> IndexedTransaction | None:
        """Return the stubbed transaction if the txid matches, else None."""
        if self._tx and self._tx.txid == txid:
            return self._tx
        return None

    def get_chain_head_round(self) -> int | None:
        """Return a fixed chain head round for verification."""
        return 100


def _request(txid: str = "A" * 52) -> CreateSuggestionRequest:
    return CreateSuggestionRequest(
        title="Add dark mode",
        body="Please add a dark mode theme for the newspaper feed UI.",
        submission_txid=txid,
    )


def _service(tx: IndexedTransaction | None) -> SuggestionService:
    return SuggestionService(
        chain_repository=StubChainRepo(tx),
        store=InMemorySuggestionStore(),
        treasury_address=TREASURY,
        min_microalgos=10_000,
    )


def test_create_suggestion_success() -> None:
    """Creates a suggestion from a valid indexed treasury payment, starting at 0 upvotes."""
    wallet = "W" * 58
    txid = "T" * 52
    tx = IndexedTransaction(
        txid=txid,
        round=10,
        intra=0,
        sender=wallet,
        txn_type="pay",
        receiver=TREASURY,
        amount_microalgos=10_000,
    )
    service = _service(tx)

    created = service.create_suggestion(wallet, _request(txid=txid))

    assert created.wallet_address == wallet
    assert created.submission_txid == txid
    assert created.status == "open"
    assert created.upvote_count == 0
    listed = service.list_open_suggestions()
    assert len(listed) == 1
    assert listed[0].upvote_count == 0


def test_create_suggestion_tx_not_indexed() -> None:
    """Rejects a submission whose txid has no matching indexed transaction."""
    service = _service(None)
    with pytest.raises(SuggestionError) as exc:
        service.create_suggestion("W" * 58, _request())
    assert exc.value.code == "tx_not_indexed"


def test_create_suggestion_wrong_sender() -> None:
    """Rejects a submission where the indexed transaction's sender isn't the claimed wallet."""
    tx = IndexedTransaction(
        txid="T" * 52,
        round=1,
        intra=0,
        sender="X" * 58,
        txn_type="pay",
        receiver=TREASURY,
        amount_microalgos=10_000,
    )
    service = _service(tx)
    with pytest.raises(SuggestionError) as exc:
        service.create_suggestion("W" * 58, _request(txid="T" * 52))
    assert exc.value.code == "tx_not_valid_submission"


def test_create_suggestion_duplicate_txid() -> None:
    """Rejects a second suggestion submitted with an already-used txid."""
    wallet = "W" * 58
    txid = "D" * 52
    tx = IndexedTransaction(
        txid=txid,
        round=1,
        intra=0,
        sender=wallet,
        txn_type="pay",
        receiver=TREASURY,
        amount_microalgos=10_000,
    )
    service = _service(tx)
    service.create_suggestion(wallet, _request(txid=txid))

    with pytest.raises(SuggestionError) as exc:
        service.create_suggestion(wallet, _request(txid=txid))
    assert exc.value.code == "duplicate_txid"
