from __future__ import annotations

import pytest

from app.modules.suggestions.models.domain import StoredSuggestion, UpvoteError
from app.modules.suggestions.services.upvote_service import UpvoteService
from app.modules.suggestions.stores.memory import InMemorySuggestionStore
from app.modules.suggestions.stores.upvote_memory import InMemoryUpvoteStore


def _always_valid(_wallet: str, _message: str, _signature: str) -> bool:
    return True


def _always_invalid(_wallet: str, _message: str, _signature: str) -> bool:
    return False


def test_upvote_success() -> None:
    wallet = "W" * 58
    store = InMemorySuggestionStore()
    suggestion_id = "s-1"
    store.insert(
        StoredSuggestion(
            suggestion_id=suggestion_id,
            wallet_address=wallet,
            title="Title here ok",
            body="Body long enough for validation rules in API.",
            submission_txid="T" * 52,
            status="open",
            created_at_epoch=1,
        )
    )
    service = UpvoteService(
        suggestion_store=store,
        upvote_store=InMemoryUpvoteStore(),
        signature_verifier=_always_valid,
    )

    result = service.upvote(
        suggestion_id=suggestion_id,
        wallet_address=wallet,
        signature_b64="c2ln",
    )
    assert result["upvote_count"] == 1


def test_upvote_invalid_signature() -> None:
    wallet = "W" * 58
    store = InMemorySuggestionStore()
    suggestion_id = "s-invalid"
    store.insert(
        StoredSuggestion(
            suggestion_id=suggestion_id,
            wallet_address=wallet,
            title="Title here ok",
            body="Body long enough for validation rules in API.",
            submission_txid="V" * 52,
            status="open",
            created_at_epoch=1,
        )
    )
    service = UpvoteService(
        suggestion_store=store,
        upvote_store=InMemoryUpvoteStore(),
        signature_verifier=_always_invalid,
    )
    with pytest.raises(UpvoteError) as exc:
        service.upvote(
            suggestion_id=suggestion_id,
            wallet_address=wallet,
            signature_b64="c2ln",
        )
    assert exc.value.code == "invalid_signature"


def test_upvote_duplicate() -> None:
    wallet = "W" * 58
    store = InMemorySuggestionStore()
    suggestion_id = "s-2"
    store.insert(
        StoredSuggestion(
            suggestion_id=suggestion_id,
            wallet_address=wallet,
            title="Title here ok",
            body="Body long enough for validation rules in API.",
            submission_txid="U" * 52,
            status="open",
            created_at_epoch=1,
        )
    )
    service = UpvoteService(
        suggestion_store=store,
        upvote_store=InMemoryUpvoteStore(),
        signature_verifier=_always_valid,
    )
    service.upvote(suggestion_id=suggestion_id, wallet_address=wallet, signature_b64="c2ln")

    with pytest.raises(UpvoteError) as exc:
        service.upvote(suggestion_id=suggestion_id, wallet_address=wallet, signature_b64="c2ln")
    assert exc.value.code == "duplicate_upvote"
