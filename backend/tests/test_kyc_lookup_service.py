"""Paid KYC lookup: charges regardless of hit/miss, pays out only the enrolled wallet."""

from __future__ import annotations

from app.modules.kyc.models.domain import StoredEnrollment
from app.modules.kyc.services.lookup_service import LookupService
from app.modules.kyc.services.payout_service import PayoutResult
from app.modules.kyc.stores.memory import InMemoryEnrollmentStore

WALLET = "W" * 58
PAYER = "P" * 58


def test_lookup_not_enrolled_charges_but_never_pays_out() -> None:
    """Returns enrolled=False for an unknown wallet and never calls the payout fn."""
    calls: list = []
    service = LookupService(
        store=InMemoryEnrollmentStore(),
        payout_fn=lambda **kw: calls.append(kw) or PayoutResult(status="sent", txid="x"),
    )

    result = service.lookup(
        wallet_address=WALLET, payer_address=PAYER, payment_txid="TX1", amount_atomic="1000000"
    )

    assert result == {"enrolled": False, "wallet_address": WALLET}
    assert calls == []  # never invoked — nothing to reward


def test_lookup_enrolled_fires_payout_to_the_looked_up_wallet_not_the_payer() -> None:
    """Pays out to the enrolled wallet address, not the paying wallet."""
    store = InMemoryEnrollmentStore()
    store.upsert(
        StoredEnrollment(
            wallet_address=WALLET,
            enrolled_at_epoch=1,
            updated_at_epoch=1,
            consent_signature_b64="sig",
            wallet_age_round=1000,
            recent_tx_count=5,
            kyc_level="established",
        )
    )
    calls: list = []

    def _fake_payout(**kwargs: object) -> PayoutResult:
        calls.append(kwargs)
        return PayoutResult(status="sent", txid="PAYOUT_TX")

    service = LookupService(store=store, payout_fn=_fake_payout)

    result = service.lookup(
        wallet_address=WALLET, payer_address=PAYER, payment_txid="TX1", amount_atomic="1000000"
    )

    assert result["enrolled"] is True
    assert result["kyc_level"] == "established"
    assert result["payout_status"] == "sent"
    assert len(calls) == 1
    # The payout goes to the ENROLLED wallet, never the payer — confirmed
    # product design, not ambiguous.
    assert calls[0]["receiver"] == WALLET
    assert calls[0]["amount_atomic"] == "1000000"


def test_lookup_records_payout_failure_but_still_returns_the_kyc_level() -> None:
    """Still returns the enrolled kyc_level even when the payout itself fails."""
    # The x402 payment is already captured before this runs and can't be
    # refunded — a payout failure must never fail the caller's response.
    store = InMemoryEnrollmentStore()
    store.upsert(
        StoredEnrollment(
            wallet_address=WALLET,
            enrolled_at_epoch=1,
            updated_at_epoch=1,
            consent_signature_b64="sig",
            wallet_age_round=1000,
            recent_tx_count=5,
            kyc_level="basic",
        )
    )
    service = LookupService(
        store=store,
        payout_fn=lambda **_kw: PayoutResult(status="failed", error="float too low"),
    )

    result = service.lookup(
        wallet_address=WALLET, payer_address=PAYER, payment_txid="TX1", amount_atomic="1000000"
    )

    assert result["enrolled"] is True
    assert result["kyc_level"] == "basic"
    assert result["payout_status"] == "failed"
