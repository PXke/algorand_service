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

from app.core.config import settings
from app.modules.x402.assets import ACCEPTED_ASSETS, AcceptedAsset

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
    """Integer atomic-unit split — floor, never round up (never pay out more than the share the platform actually keeps room for). Decimals-agnostic: `amount_atomic` is already in atomic units of whatever asset settled, so this is correct for USDC/EURQ/USDQ alike without needing to know which one it is."""
    return int(int(amount_atomic) * share)


def _asset_for(asset_id: str, network: str) -> AcceptedAsset | None:
    """The accepted asset whose ASA id on `network` matches `asset_id`, or None if `asset_id` isn't one of the marketplace's accepted assets on this network.

    `asset_id` is expected in the same shape x402.guard.PaymentResult.asset_id
    carries it: the settled payment's ASA id, as a string.
    """
    try:
        wanted = int(asset_id)
    except (TypeError, ValueError):
        return None
    for asset in ACCEPTED_ASSETS:
        if asset.asa_id_for(network) == wanted:
            return asset
    return None


def send_payout(*, receiver: str, amount_atomic: str, asset_id: str) -> PayoutResult:
    """Best-effort: sign and submit an ASA transfer of a share of the settled fee to `receiver` from the dedicated payout wallet, in the SAME asset the inbound payment actually settled in (`asset_id`, the settled ASA id as a string — see x402.guard.PaymentResult.asset_id). Never raises — every failure mode (unconfigured wallet, unrecognized settled asset, algod unreachable, opt-in missing, confirm timeout) becomes PayoutResult(status="failed"/"skipped", ...)."""
    if not settings.kyc_payout_mnemonic.strip():
        return PayoutResult(status="skipped", error="payout wallet not configured")

    amount = payout_share(amount_atomic, settings.kyc_payout_share)
    if amount <= 0:
        return PayoutResult(status="skipped", error="payout amount rounds to zero")

    network = settings.x402_network
    asset = _asset_for(asset_id, network)
    if asset is None:
        logger.warning(
            "kyc payout skipped for %s: settled asset id %r is not an accepted asset on network %s",
            receiver,
            asset_id,
            network,
        )
        return PayoutResult(status="skipped", error=f"unrecognized settled asset id {asset_id!r}")

    try:
        private_key = mnemonic.to_private_key(settings.kyc_payout_mnemonic)
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
            "kyc payout failed for %s (%s atomic of asset %s / %s): %s",
            receiver,
            amount_atomic,
            asset.symbol,
            asset_id,
            exc,
        )
        return PayoutResult(status="failed", error=str(exc))
