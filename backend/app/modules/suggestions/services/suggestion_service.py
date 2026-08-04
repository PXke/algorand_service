"""Verify a treasury payment and record a new service suggestion."""

from __future__ import annotations

import time
import uuid

from app.modules.chain.repository import ChainRepository
from app.modules.chain.verify import verify_suggestion_submission
from app.modules.suggestions.models.domain import StoredSuggestion, SuggestionError
from app.modules.suggestions.models.schemas import CreateSuggestionRequest, SuggestionResponse
from app.modules.suggestions.stores.base import SuggestionStore
from app.modules.suggestions.stores.factory import get_suggestion_store
from app.modules.suggestions.stores.upvote_factory import UpvoteStore, get_upvote_store


class SuggestionService:
    """Verify a treasury payment and record a new service suggestion."""

    def __init__(
        self,
        chain_repository: ChainRepository,
        store: SuggestionStore | None = None,
        upvote_store: UpvoteStore | None = None,
        *,
        treasury_address: str,
        min_microalgos: int,
    ) -> None:
        """Wire chain repository, stores, and the treasury-payment requirements for verification."""
        self._chain = chain_repository
        self._store = store or get_suggestion_store()
        self._upvotes = upvote_store or get_upvote_store()
        self._treasury_address = treasury_address
        self._min_microalgos = min_microalgos

    @property
    def store(self) -> SuggestionStore:
        """The underlying suggestion store."""
        return self._store

    def create_suggestion(
        self,
        wallet_address: str,
        payload: CreateSuggestionRequest,
    ) -> SuggestionResponse:
        """Verify the treasury payment and record a new suggestion."""
        if not self._treasury_address:
            raise SuggestionError(
                "treasury_not_configured",
                "PLATFORM_TREASURY_ADDRESS is not configured",
            )

        tx = self._chain.get_transaction(payload.submission_txid)
        if tx is None:
            raise SuggestionError(
                "tx_not_indexed",
                "Transaction not found in chain index; wait for Conduit to catch up or check txid",
            )
        if not verify_suggestion_submission(
            tx,
            wallet_address=wallet_address,
            treasury_address=self._treasury_address,
            min_microalgos=self._min_microalgos,
        ):
            raise SuggestionError(
                "tx_not_valid_submission",
                "Transaction must be a pay to the platform treasury meeting the minimum amount",
            )

        now = int(time.time())
        stored = StoredSuggestion(
            suggestion_id=str(uuid.uuid4()),
            wallet_address=wallet_address,
            title=payload.title,
            body=payload.body,
            submission_txid=payload.submission_txid,
            status="open",
            created_at_epoch=now,
        )
        try:
            self._store.insert(stored)
        except SuggestionError:
            raise
        return self._to_response(stored)

    def list_open_suggestions(self) -> list[SuggestionResponse]:
        """List open suggestions with their current upvote counts."""
        items = self._store.list_open()
        counts = self._upvotes.count_many([item.suggestion_id for item in items])
        return [self._to_response(item, upvote_count=counts.get(item.suggestion_id, 0)) for item in items]

    def _to_response(
        self, item: StoredSuggestion, *, upvote_count: int | None = None
    ) -> SuggestionResponse:
        return SuggestionResponse(
            suggestion_id=item.suggestion_id,
            wallet_address=item.wallet_address,
            title=item.title,
            body=item.body,
            submission_txid=item.submission_txid,
            status=item.status,
            created_at_epoch=item.created_at_epoch,
            upvote_count=(
                upvote_count
                if upvote_count is not None
                else self._upvotes.count(item.suggestion_id)
            ),
        )
