from __future__ import annotations

import base64

from algosdk import encoding, transaction
from nacl.signing import VerifyKey


def verify_auth_transaction(wallet_address: str, signing_message: str, signed_txn_b64: str) -> bool:
    """Verify a 0-ALGO self-payment used as wallet login (ARC-0025 / algo_signTxn path)."""
    try:
        # msgpack_decode expects the base64 string itself, not decoded bytes.
        signed = encoding.msgpack_decode(signed_txn_b64)
        txn = signed.transaction
        if not isinstance(txn, transaction.PaymentTxn):
            return False
        if str(txn.sender) != wallet_address or str(txn.receiver) != wallet_address:
            return False
        if int(txn.amt) != 0:
            return False
        note = txn.note.decode("utf-8") if txn.note else ""
        if note != signing_message:
            return False
        if len(note.encode("utf-8")) > 1000:
            return False
        # py-algosdk has no verify helper on SignedTransaction; check the
        # ed25519 signature over the canonical "TX"-prefixed txn bytes against
        # the sender address (which is the public key).
        if not signed.signature:
            return False
        pubkey = encoding.decode_address(wallet_address)
        to_sign = b"TX" + base64.b64decode(encoding.msgpack_encode(txn))
        VerifyKey(pubkey).verify(to_sign, base64.b64decode(signed.signature))
        return True
    except Exception:
        return False
