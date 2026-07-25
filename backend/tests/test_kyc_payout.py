"""KYC payout amount rounding and failure handling."""

from __future__ import annotations

from typing import Never

import pytest
from algosdk import account, mnemonic
from algosdk.transaction import SuggestedParams

from app.core.config import settings
from app.modules.kyc.services import payout_service
from app.modules.kyc.services.payout_service import payout_share, send_payout

_, RECEIVER = account.generate_account()


def test_payout_share_floors_never_rounds_up() -> None:
    """Floors the payout share instead of rounding it to the nearest integer."""
    assert payout_share("100", 0.5) == 50
    assert payout_share("101", 0.5) == 50  # floors, doesn't round to 51
    assert payout_share("100", 0.3) == 30


def test_send_payout_skipped_when_wallet_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the payout with a "not configured" error when no payout mnemonic is set."""
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", "")

    result = send_payout(receiver=RECEIVER, amount_atomic="1000000")

    assert result.status == "skipped"
    assert "not configured" in (result.error or "")


def test_send_payout_skipped_when_amount_rounds_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the payout with a "zero" error when the floored share amount is zero."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "kyc_payout_share", 0.5)

    result = send_payout(receiver=RECEIVER, amount_atomic="1")  # 1 * 0.5 floors to 0

    assert result.status == "skipped"
    assert "zero" in (result.error or "")


_FAKE_SP = SuggestedParams(
    fee=1000,
    first=100,
    last=1100,
    gh="4TgSl2ThJVR/A4X8V6Xh1yhQ+YlBb9DkzZH2Xu6IdMU=",
    gen="testnet-v1.0",
    flat_fee=True,
    min_fee=1000,
)


class _FakeAlgodClient:
    def __init__(self, sent: list) -> None:
        self._sent = sent

    def suggested_params(self) -> SuggestedParams:
        return _FAKE_SP

    def send_transaction(self, signed_txn: bytes) -> str:
        self._sent.append(signed_txn)
        return "FAKE_TXID_ABC"


def test_send_payout_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sends a signed payment transaction and returns the confirmed txid."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "kyc_payout_share", 0.5)
    monkeypatch.setattr(
        settings, "x402_network", "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="
    )

    sent: list = []
    monkeypatch.setattr(payout_service, "_algod_client", lambda: _FakeAlgodClient(sent))
    monkeypatch.setattr(
        payout_service, "wait_for_confirmation", lambda *_a, **_k: {"confirmed-round": 5}
    )

    result = send_payout(receiver=RECEIVER, amount_atomic="2000000")

    assert result.status == "sent"
    assert result.txid == "FAKE_TXID_ABC"
    assert len(sent) == 1


def test_send_payout_failure_is_never_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns a failed result with the error message instead of raising when algod is unreachable."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "kyc_payout_share", 0.5)

    def _boom() -> Never:
        raise ConnectionError("algod unreachable")

    monkeypatch.setattr(payout_service, "_algod_client", _boom)

    result = send_payout(receiver=RECEIVER, amount_atomic="2000000")

    assert result.status == "failed"
    assert "algod unreachable" in (result.error or "")
