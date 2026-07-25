"""Orchestrates the paid lookup: check enrollment, pay out if found, record the audit event either way. This is where the product's core rule lives — the payout always goes to the enrolled (looked-up) wallet, never the payer."""

from __future__ import annotations

from collections.abc import Callable

from app.modules.kyc.services.payout_service import PayoutResult, send_payout
from app.modules.kyc.stores.base import EnrollmentStore
from app.modules.kyc.stores.factory import get_enrollment_store

PayoutFn = Callable[..., PayoutResult]


class LookupService:
    """Paid KYC-status lookup, paying out to the enrolled wallet on a hit."""
    def __init__(
        self,
        store: EnrollmentStore | None = None,
        payout_fn: PayoutFn | None = None,
    ) -> None:
        """Wire the enrollment store and payout function, defaulting to the real implementations."""
        self._store = store or get_enrollment_store()
        self._send_payout = payout_fn or send_payout

    def lookup(
        self,
        *,
        wallet_address: str,
        payer_address: str,
        payment_txid: str,
        amount_atomic: str,
    ) -> dict[str, object]:
        """Look up a wallet's enrollment and pay out to it on a hit; always records the lookup event."""
        enrollment = self._store.get(wallet_address)

        if enrollment is None:
            # Charged regardless (same as any paid search API charging for a
            # query, not a guaranteed hit) — no payout, there's no subject to
            # reward.
            self._store.record_lookup_event(
                wallet_address=wallet_address,
                payer_address=payer_address,
                payment_txid=payment_txid,
                found=False,
                payout_status="not_applicable",
            )
            return {"enrolled": False, "wallet_address": wallet_address}

        # The payment is already captured before this runs, and x402 has no
        # refund mechanism — a payout failure must never fail this response.
        payout = self._send_payout(receiver=wallet_address, amount_atomic=amount_atomic)
        self._store.record_lookup_event(
            wallet_address=wallet_address,
            payer_address=payer_address,
            payment_txid=payment_txid,
            found=True,
            payout_status=payout.status,
            payout_txid=payout.txid,
            payout_error=payout.error,
        )
        return {
            "enrolled": True,
            "wallet_address": wallet_address,
            "kyc_level": enrollment.kyc_level,
            "wallet_age_round": enrollment.wallet_age_round,
            "recent_tx_count": enrollment.recent_tx_count,
            "payout_status": payout.status,
        }
