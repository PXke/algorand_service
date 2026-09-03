"""Payout leg: after a paid KYA lookup settles and finds an enrolled wallet, send it half the fee from a dedicated hot wallet.

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

from app.core.config import settings
from app.modules.x402.assets import asset_for_asa_id

logger = logging.getLogger(__name__)

# How many rounds to wait for the payout txn to confirm before giving up and
# reporting it as failed (it may still land later — that's fine, a payout
# failure is never fatal to the caller, see routes.py).
_CONFIRM_WAIT_ROUNDS = 4


@dataclass(frozen=True)
class PayoutResult:
    """Outcome of one KYA lookup payout attempt."""

    status: str  # "sent" | "failed" | "skipped"
    txid: str | None = None
    error: str | None = None


def _algod_client() -> AlgodClient:
    return AlgodClient(settings.algod_token, settings.algod_url)


def payout_share(amount_atomic: str, share: float) -> int:
    """Integer atomic-unit split — floor, never round up (never pay out more than the share the platform actually keeps room for). Decimals-agnostic: `amount_atomic` is already in atomic units of whatever asset settled, so this is correct for USDC/EURQ/USDQ alike without needing to know which one it is."""
    return int(int(amount_atomic) * share)


def send_payout(*, receiver: str, amount_atomic: str, asset_id: str) -> PayoutResult:
    """Best-effort: sign and submit an ASA transfer of a share of the settled fee to `receiver` from the dedicated payout wallet, in the SAME asset the inbound payment actually settled in (`asset_id`, the settled ASA id as a string — see x402.guard.PaymentResult.asset_id). Never raises — every failure mode (unconfigured wallet, a non-numeric `amount_atomic`, unrecognized settled asset, algod unreachable, opt-in missing, confirm timeout) becomes PayoutResult(status="failed"/"skipped", ...)."""
    if not settings.kyc_payout_mnemonic.strip():
        return PayoutResult(status="skipped", error="payout wallet not configured")

    try:
        amount = payout_share(amount_atomic, settings.kyc_payout_share)
    except (TypeError, ValueError):
        logger.warning(
            "kya payout skipped for %s: amount_atomic %r is not numeric", receiver, amount_atomic
        )
        return PayoutResult(
            status="skipped", error=f"amount_atomic {amount_atomic!r} is not numeric"
        )
    if amount <= 0:
        return PayoutResult(status="skipped", error="payout amount rounds to zero")

    network = settings.x402_network
    asset = asset_for_asa_id(asset_id, network)
    if asset is None:
        logger.warning(
            "kya payout skipped for %s: settled asset id %r is not an accepted asset on network %s",
            receiver,
            asset_id,
            network,
        )
        return PayoutResult(status="skipped", error=f"unrecognized settled asset id {asset_id!r}")

    # Isolated from the broad except below on purpose (audit finding
    # 2026-09-02, found while building the auto-refund mechanism that
    # copies this file's shape): the installed algosdk's
    # mnemonic.to_private_key raises ValueError(mnemonic) -- the exception
    # MESSAGE IS THE ENTIRE 25-WORD SECRET PHRASE -- when any word is not
    # in the wordlist. Letting that reach `logger.warning(..., exc, ...)`
    # below would write the secret into logs on any misconfiguration.
    # Never log str(exc) from this specific call.
    try:
        private_key = mnemonic.to_private_key(settings.kyc_payout_mnemonic)
    except Exception:
        logger.error(
            "kya payout: configured payout mnemonic is invalid (value never logged) -- "
            "the payout wallet cannot sign until this is fixed"
        )
        return PayoutResult(status="failed", error="payout mnemonic invalid")

    try:
        sender = account.address_from_private_key(private_key)
        client = _algod_client()
        params = client.suggested_params()
        asa_id = asset.asa_id_for(network)

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
        logger.warning(
            "kya payout failed for %s (%s atomic of asset %s / %s): %s",
            receiver,
            amount_atomic,
            asset.symbol,
            asset_id,
            exc,
        )
        return PayoutResult(status="failed", error=str(exc))
