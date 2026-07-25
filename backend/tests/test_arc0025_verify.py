"""ARC-0025 login-transaction verification (0-ALGO self-payment proof)."""

from __future__ import annotations

import base64

import pytest
from algosdk import account, encoding, transaction

from app.modules.auth.utils.algorand_txn_verify import verify_auth_transaction

MSG = "Sign in to algorand.pxke.me nonce=abc123"


def _signed_auth_txn(
    sk: str, addr: str, *, receiver: str | None = None, amount: int = 0, note: str = MSG
) -> str:
    sp = transaction.SuggestedParams(
        fee=1000,
        first=1000,
        last=2000,
        gh=base64.b64encode(b"x" * 32).decode(),
        flat_fee=True,
    )
    txn = transaction.PaymentTxn(
        sender=addr,
        sp=sp,
        receiver=receiver or addr,
        amt=amount,
        note=note.encode(),
    )
    return encoding.msgpack_encode(txn.sign(sk))


@pytest.fixture
def keypair() -> tuple[str, str]:
    """Generate a fresh Algorand keypair for a test."""
    return account.generate_account()


def test_valid_auth_txn_verifies(keypair: tuple[str, str]) -> None:
    """Accepts a correctly-signed 0-ALGO self-payment carrying the expected note."""
    sk, addr = keypair
    assert verify_auth_transaction(addr, MSG, _signed_auth_txn(sk, addr)) is True


def test_wrong_note_rejected(keypair: tuple[str, str]) -> None:
    """Rejects an auth transaction whose note doesn't match the expected sign-in message."""
    sk, addr = keypair
    assert verify_auth_transaction(addr, MSG, _signed_auth_txn(sk, addr, note="other")) is False


def test_nonzero_amount_rejected(keypair: tuple[str, str]) -> None:
    """Rejects an auth transaction that transfers a nonzero amount."""
    sk, addr = keypair
    assert verify_auth_transaction(addr, MSG, _signed_auth_txn(sk, addr, amount=1)) is False


def test_other_receiver_rejected(keypair: tuple[str, str]) -> None:
    """Rejects an auth transaction whose receiver isn't the sender itself."""
    sk, addr = keypair
    _, other = account.generate_account()
    assert verify_auth_transaction(addr, MSG, _signed_auth_txn(sk, addr, receiver=other)) is False


def test_signature_from_other_key_rejected(keypair: tuple[str, str]) -> None:
    """Rejects a transaction signed by a key other than the claimed sender's."""
    _, addr = keypair
    other_sk, _ = account.generate_account()
    # signed by a different key than the claimed sender
    assert verify_auth_transaction(addr, MSG, _signed_auth_txn(other_sk, addr)) is False


def test_garbage_payload_rejected(keypair: tuple[str, str]) -> None:
    """Rejects a payload that isn't valid base64/msgpack instead of raising."""
    _, addr = keypair
    assert verify_auth_transaction(addr, MSG, "not-base64!!") is False
