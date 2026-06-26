from __future__ import annotations

UPVOTE_MESSAGE_VERSION = "v1"


def build_upvote_signing_message(*, suggestion_id: str, wallet_address: str) -> str:
    """Canonical UTF-8 message wallets sign for off-chain upvotes."""
    return f"algorand-platform:upvote:{UPVOTE_MESSAGE_VERSION}:{suggestion_id}:{wallet_address}"
