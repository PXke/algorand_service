"""Payout leg: after a paid KYC lookup settles and finds an enrolled wallet, send it half the fee from a dedicated hot wallet.

Genuinely new territory for this backend — no algosdk signing code exists
anywhere else in the codebase. Deliberately isolated here: the mnemonic is
read from settings only inside this module, never passed around, and this is
the ONLY place in the backend that ever signs a transaction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from algosdk import account, mnemonic
from algosdk.transaction import AssetTransferTxn, wait_for_confirmation
from algosdk.v2client.algod import AlgodClient
from x402.mechanisms.avm.utils import get_usdc_asa_id

from app.core.config import settings

logger = logging.getLogger(__name__)

# How many rounds to wait for the payout txn to confirm before giving up and
# reporting it as failed (it may still land later — that's fine, a payout
# failure is never fatal to the caller, see routes.py).
_CONFIRM_WAIT_ROUNDS = 4


@dataclass(frozen=True)
class PayoutResult:
    """Outcome of one KYC lookup payout attempt."""
    status: str  # "sent" | "failed" | "skipped"
    txid: str | None = None
    error: str | None = None


def _algod_client() -> AlgodClient:
    return AlgodClient(settings.algod_token, settings.algod_url)


def payout_share(amount_atomic: str, share: float) -> int:
    """Integer atomic-unit split — floor, never round up (never pay out more than the share the platform actually keeps room for)."""
    return int(int(amount_atomic) * share)


def send_payout(*, receiver: str, amount_atomic: str) -> PayoutResult:
    """Best-effort: sign and submit an ASA transfer of half the settled fee to `receiver` from the dedicated payout wallet. Never raises — every failure mode (unconfigured wallet, algod unreachable, opt-in missing, confirm timeout) becomes PayoutResult(status="failed"/"skipped", ...)."""
    if not settings.kyc_payout_mnemonic.strip():
        return PayoutResult(status="skipped", error="payout wallet not configured")

    amount = payout_share(amount_atomic, settings.kyc_payout_share)
    if amount <= 0:
        return PayoutResult(status="skipped", error="payout amount rounds to zero")

    try:
        private_key = mnemonic.to_private_key(settings.kyc_payout_mnemonic)
        sender = account.address_from_private_key(private_key)
        client = _algod_client()
        params = client.suggested_params()
        asa_id = get_usdc_asa_id(settings.x402_network)

        txn = AssetTransferTxn(
            sender=sender,
            sp=params,
            receiver=receiver,
            amt=amount,
            index=asa_id,
        )
        signed = txn.sign(private_key)
        txid = client.send_transaction(signed)
        wait_for_confirmation(client, txid, _CONFIRM_WAIT_ROUNDS)
        return PayoutResult(status="sent", txid=txid)
    except Exception as exc:
        logger.warning("kyc payout failed for %s (%s atomic): %s", receiver, amount_atomic, exc)
        return PayoutResult(status="failed", error=str(exc))
