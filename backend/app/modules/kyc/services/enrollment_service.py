"""Verify consent, fetch on-chain signals, and store a wallet's KYC enrollment."""

from __future__ import annotations

import time
from collections.abc import Callable

from app.modules.kyc.models.domain import KycError, StoredEnrollment
from app.modules.kyc.services.consent_message import build_kyc_consent_message
from app.modules.kyc.services.indexer_client import WalletSignals, fetch_wallet_signals
from app.modules.kyc.stores.base import EnrollmentStore
from app.modules.kyc.stores.factory import get_enrollment_store

SignatureVerifier = Callable[[str, str, str], bool]
SignalsFetcher = Callable[[str], WalletSignals]

# Minimum recent-activity thresholds for the "established" tier — a simple,
# explicitly placeholder heuristic (not a scored model): a wallet old enough
# to have plausibly done real things, with at least a little on-chain
# activity to show for it. Ecosystem-directory linkage was considered and
# dropped for v1 (no wallet-to-service mapping exists anywhere in the
# platform yet) — revisit this heuristic once real usage data exists.
_ESTABLISHED_MIN_ROUNDS_OLD = 100_000  # ~a few days of mainnet rounds
_ESTABLISHED_MIN_RECENT_TXNS = 3


def _default_signature_verifier(wallet_address: str, message: str, signature_b64: str) -> bool:
    # MX-prefixed (Pera's algo_signData dialect), not the raw-message
    # verifier the suggestions/upvote flow uses — the KYC frontend signs
    # consent via the automated WalletConnect algo_signData call (see
    # wallet_auth_flutter's signArbitraryData), matching how this app's own
    # login flow already signs, rather than the suggestions module's
    # copy-message-sign-externally-paste-signature manual flow.
    from app.modules.auth.utils.algorand_verify import verify_signed_bytes

    return verify_signed_bytes(wallet_address, message, signature_b64)


def _derive_kyc_level(signals: WalletSignals, *, current_round: int | None) -> str:
    if signals.wallet_age_round is None:
        return "unranked"  # indexer couldn't confirm the wallet exists / signals unavailable
    age_rounds = (current_round - signals.wallet_age_round) if current_round else None
    if (
        age_rounds is not None
        and age_rounds >= _ESTABLISHED_MIN_ROUNDS_OLD
        and signals.recent_tx_count >= _ESTABLISHED_MIN_RECENT_TXNS
    ):
        return "established"
    return "basic"


class EnrollmentService:
    """Verify consent, fetch on-chain signals, and store a wallet's KYC enrollment."""

    def __init__(
        self,
        store: EnrollmentStore | None = None,
        signature_verifier: SignatureVerifier | None = None,
        signals_fetcher: SignalsFetcher | None = None,
        current_round_fetcher: Callable[[], int | None] | None = None,
    ) -> None:
        """Wire store/signature/signals/round dependencies, defaulting to the real implementations."""
        self._store = store or get_enrollment_store()
        self._verify_signature = signature_verifier or _default_signature_verifier
        self._fetch_signals = signals_fetcher or fetch_wallet_signals
        self._fetch_current_round = current_round_fetcher or (lambda: None)

    def enroll(self, *, wallet_address: str, consent_signature_b64: str) -> StoredEnrollment:
        """Verify consent, fetch wallet signals, and store the enrollment."""
        message = build_kyc_consent_message(wallet_address=wallet_address)
        if not self._verify_signature(wallet_address, message, consent_signature_b64):
            raise KycError("invalid_signature", "Signature does not match wallet or payload")

        signals = self._fetch_signals(wallet_address)
        level = _derive_kyc_level(signals, current_round=self._fetch_current_round())

        now = int(time.time())
        existing = self._store.get(wallet_address)
        record = StoredEnrollment(
            wallet_address=wallet_address,
            enrolled_at_epoch=existing.enrolled_at_epoch if existing else now,
            updated_at_epoch=now,
            consent_signature_b64=consent_signature_b64,
            wallet_age_round=signals.wallet_age_round,
            recent_tx_count=signals.recent_tx_count,
            kyc_level=level,
        )
        self._store.upsert(record)
        return record

    def get(self, wallet_address: str) -> StoredEnrollment | None:
        """Look up a wallet's stored enrollment, or None if not enrolled."""
        return self._store.get(wallet_address)
