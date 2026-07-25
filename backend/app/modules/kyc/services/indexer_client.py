"""Fetch wallet age/activity signals from the Algorand indexer."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from x402.mechanisms.avm.constants import ALGORAND_MAINNET_CAIP2

from app.core.config import settings

logger = logging.getLogger(__name__)

# How many of an account's most recent transactions to pull when computing
# the tx-activity signal. Not a full historical count (the indexer would
# paginate arbitrarily far back for an old, busy wallet) — a bounded recency
# sample is enough to distinguish "dormant" from "active" for this purpose.
_RECENT_TX_LIMIT = 50


@dataclass(frozen=True)
class WalletSignals:
    """On-chain age/activity signals fetched from the indexer for a wallet."""
    wallet_age_round: int | None
    recent_tx_count: int


def _indexer_url_for_network(network: str) -> str:
    """Backend has never needed indexer reads before (algod alone can't answer "when was this account created" — that needs an indexer, only current state). Derived from x402_network rather than a separate setting so the indexer and the payment network can never drift apart."""
    if network == ALGORAND_MAINNET_CAIP2:
        return settings.kyc_mainnet_indexer_url
    return settings.kyc_testnet_indexer_url


def fetch_wallet_signals(wallet_address: str, *, timeout: float = 8.0) -> WalletSignals:
    """Best-effort wallet age + recent activity from the public AlgoNode indexer. Fails open to WalletSignals(None, 0) — a signal computation hiccup must never block enrollment, it just means a thinner profile."""
    base_url = _indexer_url_for_network(settings.x402_network).rstrip("/")
    wallet_age_round: int | None = None
    recent_tx_count = 0

    try:
        with httpx.Client(timeout=timeout) as client:
            account_resp = client.get(f"{base_url}/v2/accounts/{wallet_address}")
            account_resp.raise_for_status()
            account = account_resp.json()
            if isinstance(account, dict):
                round_val = account.get("created-at-round")
                if isinstance(round_val, int):
                    wallet_age_round = round_val

            txn_resp = client.get(
                f"{base_url}/v2/accounts/{wallet_address}/transactions",
                params={"limit": _RECENT_TX_LIMIT},
            )
            txn_resp.raise_for_status()
            txns = txn_resp.json()
            if isinstance(txns, dict):
                transactions = txns.get("transactions")
                if isinstance(transactions, list):
                    recent_tx_count = len(transactions)
    except Exception as exc:
        logger.warning("indexer wallet-signal fetch failed for %s: %s", wallet_address, exc)

    return WalletSignals(wallet_age_round=wallet_age_round, recent_tx_count=recent_tx_count)
