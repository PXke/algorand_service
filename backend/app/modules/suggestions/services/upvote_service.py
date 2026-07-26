"""Verify a wallet signature and record an upvote on a suggestion."""

from __future__ import annotations

from collections.abc import Callable

from app.modules.suggestions.models.domain import UpvoteError
from app.modules.suggestions.services.upvote_message import build_upvote_signing_message
from app.modules.suggestions.stores.base import SuggestionStore
from app.modules.suggestions.stores.upvote_factory import UpvoteStore, get_upvote_store

SignatureVerifier = Callable[[str, str, str], bool]


def _default_signature_verifier(wallet_address: str, message: str, signature_b64: str) -> bool:
    from app.modules.auth.utils.algorand_verify import verify_wallet_signature

    return verify_wallet_signature(wallet_address, message, signature_b64)


class UpvoteService:
    """Verify a wallet signature and record an upvote on a suggestion."""

    def __init__(
        self,
        suggestion_store: SuggestionStore,
        upvote_store: UpvoteStore | None = None,
        signature_verifier: SignatureVerifier | None = None,
    ) -> None:
        """Wire the suggestion/upvote stores and signature verifier, defaulting to the real implementations."""
        self._suggestions = suggestion_store
        self._upvotes = upvote_store or get_upvote_store()
        self._verify_signature = signature_verifier or _default_signature_verifier

    def upvote(
        self,
        *,
        suggestion_id: str,
        wallet_address: str,
        signature_b64: str,
    ) -> dict[str, int | str]:
        """Verify a wallet's signature and record its upvote on a suggestion."""
        item = self._suggestions.get(suggestion_id)
        if item is None:
            raise UpvoteError("not_found", "Suggestion not found")

        message = build_upvote_signing_message(
            suggestion_id=suggestion_id,
            wallet_address=wallet_address,
        )
        if not self._verify_signature(wallet_address, message, signature_b64):
            raise UpvoteError("invalid_signature", "Signature does not match wallet or payload")

        count = self._upvotes.record_upvote(suggestion_id, wallet_address)
        return {"suggestion_id": suggestion_id, "upvote_count": count}
