"""ARC-60 (WebAuthn-shaped) signature verification for wallet auth."""

from __future__ import annotations

import base64
import hashlib
import json

from algosdk.encoding import decode_address
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.modules.auth.utils.caip122 import Caip122Message
from app.modules.auth.utils.json_canon import canonical_json_bytes


def arc0060_auth_digest(data_b64: str, authenticator_data: bytes) -> bytes:
    """ARC-0060 AUTH scope signing input (reference: assets/arc-0060/arc60wallet.api.ts)."""
    decoded = base64.b64decode(data_b64)
    client_data = json.loads(decoded.decode("utf-8"))
    canon = canonical_json_bytes(client_data)
    client_hash = hashlib.sha256(canon).digest()
    auth_hash = hashlib.sha256(authenticator_data).digest()
    return client_hash + auth_hash


def verify_arc0060_auth(
    wallet_address: str,
    *,
    data_b64: str,
    signature_b64: str,
    authenticator_data_b64: str,
    domain: str,
    expected_caip122: Caip122Message | None = None,
) -> bool:
    """Verify an ARC-60 AUTH-scope signature, and optionally cross-check its embedded CAIP-122 claims."""
    try:
        authenticator_data = base64.b64decode(authenticator_data_b64)
        rp_id_hash = hashlib.sha256(domain.encode("utf-8")).digest()
        if authenticator_data[:32] != rp_id_hash:
            return False

        digest = arc0060_auth_digest(data_b64, authenticator_data)
        public_key = decode_address(wallet_address)
        signature = base64.b64decode(signature_b64)
        VerifyKey(public_key).verify(digest, signature)

        if expected_caip122 is not None:
            payload = json.loads(base64.b64decode(data_b64).decode("utf-8"))
            parsed = Caip122Message.from_dict(payload)
            if parsed.account_address != wallet_address:
                return False
            if parsed.domain != expected_caip122.domain:
                return False
            if parsed.nonce != expected_caip122.nonce:
                return False
            if parsed.chain_id != expected_caip122.chain_id:
                return False

        return True
    except (BadSignatureError, ValueError, KeyError, json.JSONDecodeError):
        return False
    except Exception:
        return False
