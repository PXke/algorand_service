"""Build the message a wallet signs to consent to KYC enrollment."""

from __future__ import annotations

KYC_CONSENT_MESSAGE_VERSION = "v1"


def build_kyc_consent_message(*, wallet_address: str) -> str:
    """Canonical UTF-8 message a wallet signs to prove ownership + explicit opt-in to enrollment. Mirrors suggestions/services/upvote_message.py's shape — same signing convention (verify_wallet_signature), same versioned-namespace format."""
    return f"algorand-platform:kyc-consent:{KYC_CONSENT_MESSAGE_VERSION}:{wallet_address}"
