from __future__ import annotations

import base64

from algosdk.encoding import decode_address
from nacl.signing import VerifyKey


def verify_wallet_signature(wallet_address: str, message: str, signature_b64: str) -> bool:
    try:
        public_key = decode_address(wallet_address)
        signature = base64.b64decode(signature_b64)
        verify_key = VerifyKey(public_key)
        verify_key.verify(message.encode("utf-8"), signature)
        return True
    except Exception:
        return False


def verify_signed_bytes(wallet_address: str, message: str, signature_b64: str) -> bool:
    """algosdk signBytes/verifyBytes convention: the wallet signs b"MX" + data.

    This is what Pera's `signData` produces (and what SIWA verifies against),
    as opposed to `verify_wallet_signature`'s raw-message signature.
    """
    try:
        public_key = decode_address(wallet_address)
        signature = base64.b64decode(signature_b64)
        VerifyKey(public_key).verify(b"MX" + message.encode("utf-8"), signature)
        return True
    except Exception:
        return False
