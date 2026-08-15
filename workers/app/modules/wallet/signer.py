"""Agent wallet custody -- the only place this codebase signs anything with the writer agent's own Algorand key.

See backend/app/modules/kyc/services/payout_service.py for the codebase's one
other signing precedent, mirrored here. The mnemonic is read from settings
only inside this module, never passed around.

This wallet holds real MainNet ALGO (funded 2026-08-11, ~50 ALGO) -- the
original Phase 1 design ran Testnet-only specifically so nothing of value
was ever at stake; that's no longer true, by deliberate choice, and this
module's only real safety net now is handle_request()'s allowlist, not
network segregation. It must hold up on its own.

handle_request() IS the security boundary. Only two request shapes are ever
approved, and both are incapable of moving value or changing account control
even if the allowlist logic itself has a bug:

* algo_signData (Pera-dialect ARC-60): signed via algosdk.util.sign_bytes,
  which prepends the "MX" domain-separation prefix before signing -- the
  resulting signature can never be replayed as authorization for a real
  on-chain transaction. Raw dapp-supplied bytes are never signed any other
  way. Chain-agnostic -- there is no network for this path to get wrong.
* algo_signTxn (ARC-0025): approved only for a single Payment transaction
  targeting MainNet specifically (genesis_id/genesis_hash asserted, not
  merely left unchecked), with sender == receiver == the agent's own
  address, amount == 0, no close_remainder_to, and no rekey_to -- so even a
  signed txn from this path can neither move a balance nor hand control of
  the account to another key. The only real cost exposure is the network fee
  (~0.001 ALGO) on each approved request.

Everything else, including any algo_signTxn that doesn't match that exact
shape, is declined with an explicit JSON-RPC error (see wc_session.py),
never silently dropped.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequestDecision:
    """Outcome of evaluating one incoming WalletConnect JSON-RPC request."""

    approved: bool
    result: Any = None  # JSON-RPC result payload on approval -- a 1-element list, per ARC-0025/60
    decline_reason: str = ""


# Confirmed live against mainnet-api.algonode.cloud's /genesis and
# /v2/transactions/params 2026-08-11, not copied from memory.
_MAINNET_GENESIS_ID = "mainnet-v1.0"
_MAINNET_GENESIS_HASH = "wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="


def _private_key() -> str | None:
    from algosdk import mnemonic

    from app.core.config import AGENT_WALLET_MNEMONIC

    phrase = AGENT_WALLET_MNEMONIC.strip()
    if not phrase:
        return None
    try:
        return mnemonic.to_private_key(phrase)
    except Exception:
        logger.warning("AGENT_WALLET_MNEMONIC is set but invalid", exc_info=True)
        return None


def agent_wallet_address() -> str | None:
    """The agent's own MainNet address, or None if AGENT_WALLET_MNEMONIC is unset/invalid."""
    private_key = _private_key()
    if private_key is None:
        return None
    from algosdk import account

    return account.address_from_private_key(private_key)


def handle_request(method: str, params: Any) -> RequestDecision:  # noqa: ANN401 -- raw WalletConnect JSON-RPC params
    """Evaluate one incoming WalletConnect JSON-RPC request against the login-only allowlist. Never raises -- any unexpected shape or internal error declines rather than propagating."""
    try:
        if method == "algo_signData":
            return _handle_sign_data(params)
        if method == "algo_signTxn":
            return _handle_sign_txn(params)
        return RequestDecision(approved=False, decline_reason=f"method not allowed: {method}")
    except Exception as exc:
        logger.warning("wallet request handling failed for method=%s", method, exc_info=True)
        return RequestDecision(approved=False, decline_reason=f"internal error: {exc}"[:200])


def _handle_sign_data(params: Any) -> RequestDecision:  # noqa: ANN401
    address = agent_wallet_address()
    if address is None:
        return RequestDecision(approved=False, decline_reason="agent wallet not configured")
    if not isinstance(params, list) or len(params) != 1 or not isinstance(params[0], dict):
        return RequestDecision(
            approved=False, decline_reason="algo_signData: expected exactly one request item"
        )
    item = params[0]
    if str(item.get("signer") or "") != address:
        return RequestDecision(
            approved=False, decline_reason="algo_signData: signer does not match agent wallet address"
        )
    data_b64 = item.get("data")
    if not isinstance(data_b64, str) or not data_b64:
        return RequestDecision(approved=False, decline_reason="algo_signData: missing data")
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:
        return RequestDecision(approved=False, decline_reason="algo_signData: data is not valid base64")
    private_key = _private_key()
    if private_key is None:
        return RequestDecision(approved=False, decline_reason="agent wallet not configured")
    from algosdk.util import sign_bytes

    signature = sign_bytes(raw, private_key)
    return RequestDecision(approved=True, result=[signature])


def _extract_single_txn_entry(params: Any, address: str) -> tuple[dict[str, Any] | None, str]:  # noqa: ANN401
    """The lone {txn, signers} entry from an ARC-0025 request, or (None, decline_reason) for anything else."""
    if not isinstance(params, list) or not params or not isinstance(params[0], list):
        return None, "algo_signTxn: malformed request"
    txn_group = params[0]
    if len(txn_group) != 1 or not isinstance(txn_group[0], dict):
        return None, "algo_signTxn: only a single-transaction request is allowed in this phase"
    entry = txn_group[0]
    signers = entry.get("signers")
    if signers is not None and list(signers) != [address]:
        return None, "algo_signTxn: signers must be exactly the agent wallet"
    return entry, ""


def _decode_self_payment_txn(entry: dict[str, Any], address: str) -> tuple[Any, str]:
    """The decoded transaction if it's an exact 0-ALGO MainNet self-payment with no close-out/rekey, else (None, decline_reason)."""
    from algosdk import encoding
    from algosdk.transaction import PaymentTxn

    txn_b64 = entry.get("txn")
    if not isinstance(txn_b64, str) or not txn_b64:
        return None, "algo_signTxn: missing txn"
    try:
        txn = encoding.msgpack_decode(txn_b64)
    except Exception:
        return None, "algo_signTxn: could not decode transaction"
    if not isinstance(txn, PaymentTxn):
        return None, "algo_signTxn: only a Payment transaction is allowed in this phase"
    # Asserted, not merely left unchecked -- this wallet holds real MainNet
    # value (see module docstring), so a request targeting any other network
    # (or a malformed genesis) is refused rather than silently signed.
    if txn.genesis_id != _MAINNET_GENESIS_ID or txn.genesis_hash != _MAINNET_GENESIS_HASH:
        return None, "algo_signTxn: only a MainNet transaction is allowed"
    if txn.sender != address or txn.receiver != address:
        return None, "algo_signTxn: only a self-payment (sender == receiver == agent wallet) is allowed"
    if int(txn.amt or 0) != 0:
        return None, "algo_signTxn: only a 0-ALGO transaction is allowed in this phase"
    if txn.close_remainder_to or txn.rekey_to:
        return None, "algo_signTxn: close_remainder_to/rekey_to are never allowed in this phase"
    return txn, ""


def _handle_sign_txn(params: Any) -> RequestDecision:  # noqa: ANN401
    address = agent_wallet_address()
    if address is None:
        return RequestDecision(approved=False, decline_reason="agent wallet not configured")

    entry, reason = _extract_single_txn_entry(params, address)
    if entry is None:
        return RequestDecision(approved=False, decline_reason=reason)

    txn, reason = _decode_self_payment_txn(entry, address)
    if txn is None:
        return RequestDecision(approved=False, decline_reason=reason)

    private_key = _private_key()
    if private_key is None:
        return RequestDecision(approved=False, decline_reason="agent wallet not configured")

    from algosdk import encoding

    signed = txn.sign(private_key)
    return RequestDecision(approved=True, result=[encoding.msgpack_encode(signed)])
