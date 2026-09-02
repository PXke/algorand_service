"""Refund leg: send the full settled amount back to the payer when a paid route's product write fails after payment already settled.

Modeled directly on kya/services/payout_service.py's send_payout -- same
hot-wallet-signs-and-sends-ASA-transfer shape, same never-raises/best-effort
contract, same "send back in the SAME asset_id the inbound payment settled
in". Deliberately a SEPARATE dedicated wallet from both x402_pay_to_address
(receive-only, no key held) and kyc_payout_mnemonic (KYA's revenue-share
payout, a different fund for a different purpose) -- see settings.
x402_refund_mnemonic's own docstring in app/core/config.py.

This is the second place in the backend that ever signs a transaction (the
first is payout_service.py); deliberately isolated the same way, for the
same reason: the mnemonic is read from settings only inside this module,
never passed around.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from algosdk import account, mnemonic
from algosdk.transaction import AssetTransferTxn, wait_for_confirmation
from algosdk.v2client.algod import AlgodClient

from app.core.config import settings
from app.core.redis_client import get_redis
from app.modules.x402.assets import AcceptedAsset, asset_for_asa_id

logger = logging.getLogger(__name__)

# Same wait budget as payout_service.py's KYA payout -- a refund failing to
# CONFIRM within this window is reported as "sent_unconfirmed" (see
# RefundResult docstring), not "failed": the transaction was genuinely
# broadcast and will very likely still land, and this is a best-effort leg,
# not the primary response path (see paid_request.run_with_refund).
_CONFIRM_WAIT_ROUNDS = 4

_DAILY_BUDGET_KEY_PREFIX = "algorand:x402:refund_daily:"
# A day plus slack, so a key from a run that started just before midnight
# UTC still expires cleanly rather than lingering into the next day's count.
_DAILY_BUDGET_KEY_TTL_SECONDS = 90_000


@dataclass(frozen=True)
class RefundResult:
    """Outcome of one refund attempt.

    status is one of:
    * "sent" -- broadcast AND confirmed. txid is set.
    * "sent_unconfirmed" -- broadcast succeeded but confirmation could not be
      verified within the wait budget (network congestion, a transient algod
      hiccup). txid IS still set -- found-in-audit gap (2026-09-02): the
      previous version discarded the txid here and reported "failed",
      which meant a transaction that was very likely about to confirm had
      no on-chain reference left anywhere, risking a manual double-pay if
      an operator (or the payer, disputing it) later "reconciled" a refund
      that had actually already gone out. Reconciliation for this status
      must start with "look up this txid on-chain", never "send it again".
    * "failed" -- never broadcast, or broadcast definitively rejected. No
      funds moved. txid is None.
    * "skipped" -- refund wallet not configured, non-positive/malformed
      amount, unrecognized asset, or the daily budget for this asset is
      exhausted. No funds moved, no on-chain attempt was made at all.
    """

    status: str  # "sent" | "sent_unconfirmed" | "failed" | "skipped"
    txid: str | None = None
    error: str | None = None


def _algod_client() -> AlgodClient:
    return AlgodClient(settings.algod_token, settings.algod_url)


def _daily_budget_key(asset_id: str) -> str:
    today = datetime.now(tz=UTC).date().isoformat()
    return f"{_DAILY_BUDGET_KEY_PREFIX}{asset_id}:{today}"


def _reserve_daily_budget(*, asset_id: str, amount: int) -> bool:
    """Atomically reserve `amount` against today's per-asset refund budget. True if reserved, False if it would exceed the budget or Redis is unreachable (fails CLOSED -- this guards money leaving the wallet, same reasoning as circuit_breaker.py).

    Reserve-then-undo-on-failure, same pattern this codebase already uses
    for promo redemption caps: increment first (atomic), and if the NEW
    total is over budget, decrement back out rather than checking-then-
    incrementing, which would race under concurrent refunds.
    """
    key = _daily_budget_key(asset_id)
    try:
        client = get_redis()
        new_total = int(client.incrby(key, amount))
        if new_total == amount:  # first write to this key today
            client.expire(key, _DAILY_BUDGET_KEY_TTL_SECONDS)
    except Exception:
        logger.error(
            "x402 refund: could not verify today's refund budget for asset_id=%s -- "
            "failing CLOSED (refund skipped)",
            asset_id,
            exc_info=True,
        )
        return False

    if new_total > settings.x402_refund_daily_budget_atomic:
        try:
            get_redis().decrby(key, amount)
        except Exception:
            logger.error(
                "x402 refund: reserved %d atomic of asset_id=%s over budget and could not "
                "release the reservation -- today's counter for this asset is now "
                "overstated by that amount until it expires",
                amount,
                asset_id,
                exc_info=True,
            )
        logger.error(
            "x402 refund: daily budget exhausted for asset_id=%s (attempted total %d, "
            "budget %d) -- refund skipped, not sent",
            asset_id,
            new_total,
            settings.x402_refund_daily_budget_atomic,
        )
        return False
    return True


def _prepare_refund(
    *, receiver: str, amount_atomic: str, asset_id: str
) -> tuple[int, AcceptedAsset] | RefundResult:
    """Validate + reserve budget for one refund attempt. Returns (amount, asset) to proceed, or a terminal RefundResult to return as-is.

    Extracted from send_refund purely to stay under the complexity budget --
    every check here is a plain early-exit, no shared state beyond the
    daily-budget reservation (which is itself already released on its own
    failure path inside _reserve_daily_budget).
    """
    if not settings.x402_refund_mnemonic.strip():
        return RefundResult(status="skipped", error="refund wallet not configured")

    if not receiver:
        return RefundResult(status="failed", error="no payer address to refund")

    try:
        amount = int(str(amount_atomic or "").strip())
    except ValueError:
        return RefundResult(
            status="failed", error=f"amount_atomic is not an integer: {amount_atomic!r}"
        )
    if amount <= 0:
        return RefundResult(status="skipped", error="refund amount is not positive")

    network = settings.x402_network
    asset = asset_for_asa_id(asset_id, network)
    if asset is None:
        logger.warning(
            "x402 refund skipped for %s: settled asset id %r is not an accepted asset on "
            "network %s",
            receiver,
            asset_id,
            network,
        )
        return RefundResult(status="skipped", error=f"unrecognized settled asset id {asset_id!r}")

    if not _reserve_daily_budget(asset_id=asset_id, amount=amount):
        return RefundResult(status="skipped", error="daily refund budget exhausted")

    return amount, asset


def send_refund(*, receiver: str, amount_atomic: str, asset_id: str) -> RefundResult:
    """Best-effort: sign and submit an ASA transfer of the FULL `amount_atomic` back to `receiver` (the original payer) from the dedicated refund wallet, in the SAME asset the inbound payment actually settled in.

    Never raises -- every failure mode (unconfigured wallet, unrecognized
    settled asset, a non-integer amount, budget exhausted, algod
    unreachable, opt-in missing, confirm timeout) becomes a RefundResult.
    Unlike send_payout's payout_share, this sends back the FULL amount, not
    a percentage -- a refund is undoing a charge, not splitting a fee.
    """
    prepared = _prepare_refund(receiver=receiver, amount_atomic=amount_atomic, asset_id=asset_id)
    if isinstance(prepared, RefundResult):
        return prepared
    amount, asset = prepared

    # Isolated from the broad except below on purpose (audit finding
    # 2026-09-02): the installed algosdk's mnemonic.to_private_key raises
    # ValueError(mnemonic) -- the exception MESSAGE IS THE ENTIRE 25-WORD
    # SECRET PHRASE -- when any word is not in the wordlist. Letting that
    # reach `logger.warning(..., exc, ...)` below would write the secret
    # into logs on any misconfiguration. Never log str(exc) from this call.
    try:
        private_key = mnemonic.to_private_key(settings.x402_refund_mnemonic)
    except Exception:
        logger.error(
            "x402 refund: configured refund mnemonic is invalid (value never logged) -- "
            "the refund wallet cannot sign until this is fixed"
        )
        return RefundResult(status="failed", error="refund mnemonic invalid")

    try:
        sender = account.address_from_private_key(private_key)
        client = _algod_client()
        params = client.suggested_params()
        asa_id = asset.asa_id_for(settings.x402_network)

        txn = AssetTransferTxn(
            sender=sender,
            sp=params,
            receiver=receiver,
            amt=amount,
            index=asa_id,
        )
        signed = txn.sign(private_key)
    except Exception as exc:
        logger.warning(
            "x402 refund failed to build/sign for %s (%s atomic of asset %s / %s): %s",
            receiver,
            amount_atomic,
            asset.symbol,
            asset_id,
            exc,
        )
        return RefundResult(status="failed", error=str(exc))

    try:
        txid = client.send_transaction(signed)
    except Exception as exc:
        logger.warning(
            "x402 refund broadcast failed for %s (%s atomic of asset %s / %s): %s",
            receiver,
            amount_atomic,
            asset.symbol,
            asset_id,
            exc,
        )
        return RefundResult(status="failed", error=str(exc))

    try:
        wait_for_confirmation(client, txid, _CONFIRM_WAIT_ROUNDS)
        return RefundResult(status="sent", txid=txid)
    except Exception as exc:
        # Broadcast succeeded -- txid is real and very likely to confirm --
        # only the CONFIRMATION check itself failed. Keep the txid (see
        # RefundResult's own docstring for why this status exists).
        logger.warning(
            "x402 refund broadcast for %s but confirmation could not be verified "
            "(%s atomic of asset %s / %s, tx=%s): %s",
            receiver,
            amount_atomic,
            asset.symbol,
            asset_id,
            txid,
            exc,
        )
        return RefundResult(status="sent_unconfirmed", txid=txid, error=str(exc))
