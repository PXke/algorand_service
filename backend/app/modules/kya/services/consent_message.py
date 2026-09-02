"""Build the message a wallet signs to consent to KYA enrollment."""

from __future__ import annotations

KYC_CONSENT_MESSAGE_VERSION = "v2"


def build_kyc_consent_message(*, wallet_address: str, nonce: str, expires_at: int) -> str:
    """Canonical UTF-8 message a wallet signs to prove ownership + explicit opt-in to enrollment.

    `nonce` and `expires_at` come from the consent-message endpoint (stored in
    Redis and consumed once at enroll) so a captured signature cannot be
    replayed after expiry or a successful enroll. Mirrors
    suggestions/services/upvote_message.py's shape — same signing convention
    (verify_wallet_signature), same versioned-namespace format.
    """
    return (
        f"algorand-platform:kyc-consent:{KYC_CONSENT_MESSAGE_VERSION}"
        f":{wallet_address}:{nonce}:{expires_at}"
    )
