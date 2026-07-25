"""Signature verification and enrollment storage for the KYC enroll flow."""

from __future__ import annotations

import pytest
from algosdk import account, util

from app.modules.kyc.models.domain import KycError
from app.modules.kyc.services.consent_message import build_kyc_consent_message
from app.modules.kyc.services.enrollment_service import (
    EnrollmentService,
    _default_signature_verifier,
    _derive_kyc_level,
)
from app.modules.kyc.services.indexer_client import WalletSignals
from app.modules.kyc.stores.memory import InMemoryEnrollmentStore

WALLET = "W" * 58


def _always_valid(_wallet: str, _message: str, _signature: str) -> bool:
    return True


def _always_invalid(_wallet: str, _message: str, _signature: str) -> bool:
    return False


def test_default_signature_verifier_accepts_real_pera_dialect_signature() -> None:
    """Accepts an MX-prefixed algosdk signBytes signature (the Pera/WalletConnect signData convention)."""
    # The KYC frontend signs consent via the automated WalletConnect
    # algo_signData call (wallet_auth_flutter's signArbitraryData), which
    # produces an MX-prefixed algosdk signBytes signature — confirms the
    # default verifier actually checks THAT convention, not the suggestions
    # module's raw-message one (they are not interchangeable).
    sk, addr = account.generate_account()
    message = build_kyc_consent_message(wallet_address=addr)
    sig = util.sign_bytes(message.encode(), sk)

    assert _default_signature_verifier(addr, message, sig) is True


def test_default_signature_verifier_rejects_raw_non_mx_signature() -> None:
    """Rejects a raw (non-MX-prefixed) signature that the suggestions verifier would accept."""
    import base64

    from nacl.signing import SigningKey

    sk, addr = account.generate_account()
    message = build_kyc_consent_message(wallet_address=addr)
    # Raw signature (no "MX" prefix) — what the suggestions module's
    # verify_wallet_signature accepts, but the KYC default must not.
    private_key_bytes = base64.b64decode(sk)[:32]
    raw_sig = base64.b64encode(
        SigningKey(private_key_bytes).sign(message.encode()).signature
    ).decode()

    assert _default_signature_verifier(addr, message, raw_sig) is False


def test_enroll_success_stores_signals_and_level() -> None:
    """A valid enrollment stores the wallet's signals and derived KYC level in the store."""
    store = InMemoryEnrollmentStore()
    service = EnrollmentService(
        store=store,
        signature_verifier=_always_valid,
        signals_fetcher=lambda _addr: WalletSignals(wallet_age_round=1000, recent_tx_count=5),
        current_round_fetcher=lambda: 200_000,
    )

    record = service.enroll(wallet_address=WALLET, consent_signature_b64="c2ln")

    assert record.wallet_address == WALLET
    assert record.wallet_age_round == 1000
    assert record.recent_tx_count == 5
    assert record.kyc_level == "established"
    assert store.get(WALLET) is record


def test_enroll_invalid_signature_raises() -> None:
    """Raises a KycError with code invalid_signature when the consent signature fails verification."""
    service = EnrollmentService(
        store=InMemoryEnrollmentStore(),
        signature_verifier=_always_invalid,
        signals_fetcher=lambda _addr: WalletSignals(wallet_age_round=1000, recent_tx_count=5),
    )

    with pytest.raises(KycError) as exc:
        service.enroll(wallet_address=WALLET, consent_signature_b64="c2ln")
    assert exc.value.code == "invalid_signature"


def test_enroll_never_calls_signals_fetcher_when_signature_invalid() -> None:
    """Skips the indexer signals fetch entirely when the signature check fails first."""
    # Don't burn an indexer call verifying signals for a request that fails
    # ownership proof.
    calls: list[str] = []

    def _tracking_fetcher(addr: str) -> WalletSignals:
        calls.append(addr)
        return WalletSignals(wallet_age_round=1, recent_tx_count=0)

    service = EnrollmentService(
        store=InMemoryEnrollmentStore(),
        signature_verifier=_always_invalid,
        signals_fetcher=_tracking_fetcher,
    )
    with pytest.raises(KycError):
        service.enroll(wallet_address=WALLET, consent_signature_b64="c2ln")
    assert calls == []


def test_reenrollment_preserves_original_enrolled_at() -> None:
    """Re-enrolling the same wallet keeps the original enrolled_at but updates the signature."""
    store = InMemoryEnrollmentStore()
    service = EnrollmentService(
        store=store,
        signature_verifier=_always_valid,
        signals_fetcher=lambda _addr: WalletSignals(wallet_age_round=1000, recent_tx_count=1),
    )

    first = service.enroll(wallet_address=WALLET, consent_signature_b64="c2ln")
    second = service.enroll(wallet_address=WALLET, consent_signature_b64="c2ln2")

    assert second.enrolled_at_epoch == first.enrolled_at_epoch
    assert second.consent_signature_b64 == "c2ln2"


def test_derive_kyc_level_unranked_when_indexer_has_no_signal() -> None:
    """Derives "unranked" when the indexer has no wallet-age signal at all."""
    signals = WalletSignals(wallet_age_round=None, recent_tx_count=0)
    assert _derive_kyc_level(signals, current_round=200_000) == "unranked"


def test_derive_kyc_level_basic_when_too_new_or_too_quiet() -> None:
    """Derives "basic" when the wallet is old but inactive, or active but too new."""
    # Old enough but not active enough.
    quiet = WalletSignals(wallet_age_round=1000, recent_tx_count=0)
    assert _derive_kyc_level(quiet, current_round=200_000) == "basic"
    # Active enough but too new.
    new = WalletSignals(wallet_age_round=199_999, recent_tx_count=10)
    assert _derive_kyc_level(new, current_round=200_000) == "basic"


def test_derive_kyc_level_established_when_old_and_active() -> None:
    """Derives "established" when the wallet is both old enough and recently active."""
    signals = WalletSignals(wallet_age_round=1000, recent_tx_count=5)
    assert _derive_kyc_level(signals, current_round=200_000) == "established"


def test_derive_kyc_level_basic_when_current_round_unavailable() -> None:
    """Falls back to "basic" when there is no current round to measure wallet age against."""
    # Can't compute age in rounds without a head round to compare against —
    # never claim "established" without being able to check the threshold.
    signals = WalletSignals(wallet_age_round=1000, recent_tx_count=5)
    assert _derive_kyc_level(signals, current_round=None) == "basic"
