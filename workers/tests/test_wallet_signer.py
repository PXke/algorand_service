"""Agent wallet signer allowlist: the actual security boundary for Phase 1 (WalletConnect login only, no value-moving capability). Every case here either approves a request that provably cannot move value / change account control, or declines."""

from __future__ import annotations

import base64

import pytest
from algosdk import account, encoding
from algosdk.transaction import AssetTransferTxn, PaymentTxn, SuggestedParams
from algosdk.util import verify_bytes
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.modules.wallet import signer


@pytest.fixture
def wallet(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure a fresh agent wallet for one test and return its address."""
    from algosdk import mnemonic as mnemonic_mod

    sk, addr = account.generate_account()
    phrase = mnemonic_mod.from_private_key(sk)
    monkeypatch.setattr("app.core.config.AGENT_WALLET_MNEMONIC", phrase)
    return addr


def _suggested_params(*, gen: str = signer._MAINNET_GENESIS_ID, gh: str = signer._MAINNET_GENESIS_HASH) -> SuggestedParams:
    return SuggestedParams(
        fee=0,
        first=1000,
        last=2000,
        gh=gh,
        gen=gen,
        flat_fee=False,
        min_fee=1000,
    )


def test_agent_wallet_address_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """No AGENT_WALLET_MNEMONIC -- address resolution returns None, not an exception."""
    monkeypatch.setattr("app.core.config.AGENT_WALLET_MNEMONIC", "")
    assert signer.agent_wallet_address() is None


def test_handle_request_declines_unknown_method(wallet: str) -> None:
    """Any JSON-RPC method other than algo_signData/algo_signTxn is declined outright."""
    assert wallet  # wallet must be configured to reach the method dispatch, not the "unconfigured" branch
    decision = signer.handle_request("algo_signAnythingElse", [{}])
    assert not decision.approved
    assert "not allowed" in decision.decline_reason


# ---------------------------------------------------------------------------
# algo_signData (Pera-dialect ARC-60 login signature)
# ---------------------------------------------------------------------------


def test_sign_data_approves_matching_signer_and_applies_mx_prefix(wallet: str) -> None:
    """A well-formed request from our own address is approved, and the signature only verifies with the MX prefix applied -- never against the raw bytes."""
    raw = b"login-challenge-bytes"
    params = [
        {
            "data": base64.b64encode(raw).decode(),
            "message": "Sign in to example dapp",
            "signer": wallet,
            "chainId": 416002,
        }
    ]
    decision = signer.handle_request("algo_signData", params)
    assert decision.approved
    signature = decision.result[0]

    # The signature must verify against the MX-prefixed message (the safe,
    # standard path) ...
    assert verify_bytes(raw, signature, wallet) is True
    # ... and must NOT verify against the raw, unprefixed bytes -- proving
    # this can never be replayed as a real transaction signature.
    verify_key = VerifyKey(encoding.decode_address(wallet))
    with pytest.raises(BadSignatureError):
        verify_key.verify(raw, base64.b64decode(signature))


def test_sign_data_declines_signer_mismatch(wallet: str) -> None:
    """A request naming a different address as the signer is declined, not silently signed by us anyway."""
    assert wallet  # wallet must be configured so the decline is genuinely about the mismatch
    _other_sk, other_addr = account.generate_account()
    params = [
        {
            "data": base64.b64encode(b"x").decode(),
            "message": "m",
            "signer": other_addr,
            "chainId": 416002,
        }
    ]
    decision = signer.handle_request("algo_signData", params)
    assert not decision.approved
    assert "signer does not match" in decision.decline_reason


def test_sign_data_declines_malformed_request_shape(wallet: str) -> None:
    """More than one item in the request array is never accepted."""
    decision = signer.handle_request("algo_signData", [{"signer": wallet}, {"extra": 1}])
    assert not decision.approved


def test_sign_data_declines_when_wallet_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """No agent wallet configured -- a clean decline, never a crash."""
    monkeypatch.setattr("app.core.config.AGENT_WALLET_MNEMONIC", "")
    decision = signer.handle_request(
        "algo_signData", [{"data": "AA==", "message": "m", "signer": "X", "chainId": 1}]
    )
    assert not decision.approved
    assert "not configured" in decision.decline_reason


# ---------------------------------------------------------------------------
# algo_signTxn (ARC-0025) -- only an exact 0-ALGO self-payment is ever approved
# ---------------------------------------------------------------------------


def _txn_entry(txn: object, signers: list[str] | None = None) -> dict:
    entry: dict = {"txn": encoding.msgpack_encode(txn)}
    if signers is not None:
        entry["signers"] = signers
    return entry


def test_sign_txn_approves_zero_algo_self_payment(wallet: str) -> None:
    """The one shape this phase allows: sender == receiver == agent wallet, amount 0."""
    txn = PaymentTxn(sender=wallet, sp=_suggested_params(), receiver=wallet, amt=0)
    decision = signer.handle_request("algo_signTxn", [[_txn_entry(txn, [wallet])], {}])
    assert decision.approved
    signed_b64 = decision.result[0]
    signed = encoding.msgpack_decode(signed_b64)
    assert signed.transaction.sender == wallet
    assert signed.transaction.receiver == wallet
    assert signed.transaction.amt == 0


def test_sign_txn_declines_wrong_network_genesis_id(wallet: str) -> None:
    """A transaction built for any network other than MainNet is declined -- this wallet holds real MainNet ALGO, so the network is asserted, not just left unchecked."""
    txn = PaymentTxn(sender=wallet, sp=_suggested_params(gen="testnet-v1.0"), receiver=wallet, amt=0)
    decision = signer.handle_request("algo_signTxn", [[_txn_entry(txn, [wallet])], {}])
    assert not decision.approved
    assert "MainNet" in decision.decline_reason


def test_sign_txn_declines_wrong_network_genesis_hash(wallet: str) -> None:
    """Even a correct-looking genesis_id with a mismatched genesis_hash is declined -- both fields must match MainNet."""
    txn = PaymentTxn(
        sender=wallet,
        sp=_suggested_params(gh=base64.b64encode(b"x" * 32).decode()),
        receiver=wallet,
        amt=0,
    )
    decision = signer.handle_request("algo_signTxn", [[_txn_entry(txn, [wallet])], {}])
    assert not decision.approved
    assert "MainNet" in decision.decline_reason


def test_sign_txn_declines_nonzero_amount(wallet: str) -> None:
    """A self-payment for any nonzero amount is declined -- 0-ALGO only."""
    txn = PaymentTxn(sender=wallet, sp=_suggested_params(), receiver=wallet, amt=1_000_000)
    decision = signer.handle_request("algo_signTxn", [[_txn_entry(txn, [wallet])], {}])
    assert not decision.approved
    assert "0-ALGO" in decision.decline_reason


def test_sign_txn_declines_different_receiver(wallet: str) -> None:
    """A payment to any address other than ourselves is declined."""
    _other_sk, other_addr = account.generate_account()
    txn = PaymentTxn(sender=wallet, sp=_suggested_params(), receiver=other_addr, amt=0)
    decision = signer.handle_request("algo_signTxn", [[_txn_entry(txn, [wallet])], {}])
    assert not decision.approved
    assert "self-payment" in decision.decline_reason


def test_sign_txn_declines_asset_transfer(wallet: str) -> None:
    """Only a Payment transaction type is ever allowed -- an ASA transfer is declined even at 0 amount."""
    txn = AssetTransferTxn(sender=wallet, sp=_suggested_params(), receiver=wallet, amt=0, index=1)
    decision = signer.handle_request("algo_signTxn", [[_txn_entry(txn, [wallet])], {}])
    assert not decision.approved
    assert "Payment transaction" in decision.decline_reason


def test_sign_txn_declines_close_remainder_to(wallet: str) -> None:
    """A 0-ALGO self-payment that also closes the account out to another address is declined -- close-out is never allowed even disguised as a zero-amount txn."""
    _other_sk, other_addr = account.generate_account()
    txn = PaymentTxn(
        sender=wallet, sp=_suggested_params(), receiver=wallet, amt=0, close_remainder_to=other_addr
    )
    decision = signer.handle_request("algo_signTxn", [[_txn_entry(txn, [wallet])], {}])
    assert not decision.approved
    assert "close_remainder_to" in decision.decline_reason


def test_sign_txn_declines_rekey_to(wallet: str) -> None:
    """A 0-ALGO self-payment that also rekeys the account to another key is declined -- rekeying is never allowed."""
    _other_sk, other_addr = account.generate_account()
    txn = PaymentTxn(sender=wallet, sp=_suggested_params(), receiver=wallet, amt=0, rekey_to=other_addr)
    decision = signer.handle_request("algo_signTxn", [[_txn_entry(txn, [wallet])], {}])
    assert not decision.approved
    assert "rekey_to" in decision.decline_reason


def test_sign_txn_declines_multi_transaction_group(wallet: str) -> None:
    """More than one transaction in the group is declined, even if every entry individually would qualify."""
    txn = PaymentTxn(sender=wallet, sp=_suggested_params(), receiver=wallet, amt=0)
    entry = _txn_entry(txn, [wallet])
    decision = signer.handle_request("algo_signTxn", [[entry, entry], {}])
    assert not decision.approved
    assert "single-transaction" in decision.decline_reason


def test_sign_txn_declines_signer_field_mismatch(wallet: str) -> None:
    """A request whose signers field names someone other than the agent wallet is declined."""
    _other_sk, other_addr = account.generate_account()
    txn = PaymentTxn(sender=wallet, sp=_suggested_params(), receiver=wallet, amt=0)
    decision = signer.handle_request("algo_signTxn", [[_txn_entry(txn, [other_addr])], {}])
    assert not decision.approved
    assert "signers must be" in decision.decline_reason
