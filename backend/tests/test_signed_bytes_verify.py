"""signed_bytes login proof (Pera signData / algosdk signBytes: sig over b"MX"+msg)."""

from __future__ import annotations

import base64

import pytest
from algosdk import account, util

from app.modules.auth.utils.algorand_verify import verify_signed_bytes, verify_wallet_signature

MSG = "algorand.pxke.me wants you to sign in with your Algorand account nonce=abc123"


@pytest.fixture()
def keypair() -> tuple[str, str]:
    return account.generate_account()


def test_signbytes_signature_verifies(keypair):
    sk, addr = keypair
    # algosdk sign_bytes prepends b"MX" — exactly what Pera's signData produces.
    sig = util.sign_bytes(MSG.encode(), sk)
    assert verify_signed_bytes(addr, MSG, sig) is True


def test_wrong_message_rejected(keypair):
    sk, addr = keypair
    sig = util.sign_bytes(MSG.encode(), sk)
    assert verify_signed_bytes(addr, "tampered", sig) is False


def test_signature_from_other_key_rejected(keypair):
    _, addr = keypair
    other_sk, _ = account.generate_account()
    sig = util.sign_bytes(MSG.encode(), other_sk)
    assert verify_signed_bytes(addr, MSG, sig) is False


def test_garbage_signature_rejected(keypair):
    _, addr = keypair
    assert verify_signed_bytes(addr, MSG, "not-base64!!") is False


def test_mx_prefixed_sig_does_not_pass_legacy_verifier(keypair):
    """The two proof formats must not be interchangeable."""
    sk, addr = keypair
    sig = util.sign_bytes(MSG.encode(), sk)
    assert verify_wallet_signature(addr, MSG, sig) is False


def test_raw_sig_does_not_pass_signed_bytes(keypair):
    sk, addr = keypair
    import nacl.signing

    raw_sk = base64.b64decode(sk)[:32]
    raw_sig = nacl.signing.SigningKey(raw_sk).sign(MSG.encode()).signature
    assert verify_signed_bytes(addr, MSG, base64.b64encode(raw_sig).decode()) is False
