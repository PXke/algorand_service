from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.modules.auth.utils.caip122 import Caip122Message, utc_now_iso
from app.modules.auth.utils.siwa_message import prepare_siwa_from_caip122


@dataclass
class AuthChallenge:
    nonce: str
    signing_message: str
    caip122: Caip122Message


def build_auth_challenge(nonce: str, wallet_address: str) -> AuthChallenge:
    """Build SIWA display message + CAIP-122 JSON for ARC-0060 AUTH."""
    issued_at = utc_now_iso()
    expiration = (
        (datetime.now(UTC) + timedelta(seconds=settings.nonce_ttl_seconds))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    caip122 = Caip122Message(
        domain=settings.auth_domain,
        account_address=wallet_address,
        uri=settings.auth_uri,
        chain_id=settings.auth_caip2_chain_id,
        nonce=nonce,
        statement=settings.auth_statement,
        issued_at=issued_at,
        expiration_time=expiration,
        type="ed25519",
    )
    signing_message = prepare_siwa_from_caip122(
        caip122,
        wallet_connect_chain_id=settings.auth_wallet_connect_chain_id,
    )
    return AuthChallenge(nonce=nonce, signing_message=signing_message, caip122=caip122)


def build_signing_message(nonce: str, wallet_address: str) -> str:
    """Backward-compatible helper returning only the SIWA string."""
    return build_auth_challenge(nonce, wallet_address).signing_message
