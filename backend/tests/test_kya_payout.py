"""KYA payout amount rounding, settled-asset selection, and failure handling."""

from __future__ import annotations

from typing import Never

import pytest
from algosdk import account, mnemonic
from algosdk.transaction import SuggestedParams
from x402.mechanisms.avm.constants import ALGORAND_MAINNET_CAIP2

from app.core.config import settings
from app.modules.kya.services import payout_service
from app.modules.kya.services.payout_service import payout_share, send_payout
from app.modules.x402.assets import EURQ, USDC, USDQ

_, RECEIVER = account.generate_account()

# ASA id of USDC on the TestNet network settings.x402_network defaults to
# (see app/core/config.py) — used as the `asset_id` for tests that don't care
# which asset it is, only that the payout doesn't blow up before reaching it.
_DEFAULT_NETWORK_USDC_ASSET_ID = "10458941"


def test_payout_share_floors_never_rounds_up() -> None:
    """Floors the payout share instead of rounding it to the nearest integer."""
    assert payout_share("100", 0.5) == 50
    assert payout_share("101", 0.5) == 50  # floors, doesn't round to 51
    assert payout_share("100", 0.3) == 30


def test_send_payout_skipped_when_wallet_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the payout with a "not configured" error when no payout mnemonic is set."""
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", "")

    result = send_payout(
        receiver=RECEIVER, amount_atomic="1000000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert result.status == "skipped"
    assert "not configured" in (result.error or "")


def test_send_payout_invalid_mnemonic_never_logs_the_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Found-in-audit fix (2026-09-02): the installed algosdk's mnemonic.to_private_key raises ValueError(mnemonic) -- the exception MESSAGE IS THE ENTIRE 25-WORD SECRET -- on a misconfigured mnemonic. That secret must never reach a log line."""
    real_secret = "word1 word2 word3 not a real valid mnemonic phrase at all"
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", real_secret)

    with caplog.at_level("DEBUG"):
        result = send_payout(
            receiver=RECEIVER, amount_atomic="1000000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
        )

    assert result.status == "failed"
    assert "mnemonic invalid" in (result.error or "")
    assert real_secret not in (result.error or "")
    for record in caplog.records:
        assert real_secret not in record.getMessage()
        assert "word1" not in record.getMessage()


def test_send_payout_skipped_when_amount_rounds_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the payout with a "zero" error when the floored share amount is zero."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "kyc_payout_share", 0.5)

    result = send_payout(
        receiver=RECEIVER, amount_atomic="1", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )  # 1 * 0.5 floors to 0

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

    result = send_payout(
        receiver=RECEIVER, amount_atomic="2000000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert result.status == "sent"
    assert result.txid == "FAKE_TXID_ABC"
    assert len(sent) == 1
    # Pays out in the SAME asset that settled (USDC on TestNet here), not a
    # hardcoded id that happens to match by coincidence.
    assert sent[0].transaction.index == USDC.asa_id_for(settings.x402_network)


def test_send_payout_failure_is_never_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns a failed result with the error message instead of raising when algod is unreachable."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "kyc_payout_share", 0.5)

    def _boom() -> Never:
        raise ConnectionError("algod unreachable")

    monkeypatch.setattr(payout_service, "_algod_client", _boom)

    result = send_payout(
        receiver=RECEIVER, amount_atomic="2000000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert result.status == "failed"
    assert "algod unreachable" in (result.error or "")


def test_send_payout_pays_out_in_the_settled_asset_not_hardcoded_usdc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a lookup fee that settled in EURQ must pay out in EURQ, not USDC.

    Before this fix, send_payout always built the ASA transfer with
    get_usdc_asa_id(...) regardless of which asset actually settled — a real
    funds bug once EURQ/USDQ were accepted. EURQ only has a mainnet ASA id
    (see app/modules/x402/assets.py), so this exercises it on mainnet.
    """
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "kyc_payout_share", 0.5)
    monkeypatch.setattr(settings, "x402_network", ALGORAND_MAINNET_CAIP2)

    sent: list = []
    monkeypatch.setattr(payout_service, "_algod_client", lambda: _FakeAlgodClient(sent))
    monkeypatch.setattr(
        payout_service, "wait_for_confirmation", lambda *_a, **_k: {"confirmed-round": 5}
    )

    eurq_asset_id = str(EURQ.asa_id_for(ALGORAND_MAINNET_CAIP2))
    result = send_payout(receiver=RECEIVER, amount_atomic="2000000", asset_id=eurq_asset_id)

    assert result.status == "sent"
    assert len(sent) == 1
    sent_asset_id = sent[0].transaction.index
    assert sent_asset_id == EURQ.asa_id_for(ALGORAND_MAINNET_CAIP2)
    assert sent_asset_id != USDC.asa_id_for(ALGORAND_MAINNET_CAIP2)
    # The split is computed on atomic units of whatever asset settled, and
    # EURQ/USDQ/USDC all currently share 6 decimals, so the floored amount is
    # identical to the USDC case above — this asserts that stays true rather
    # than assuming it silently.
    assert EURQ.decimals == USDC.decimals == USDQ.decimals == 6
    assert sent[0].transaction.amount == payout_share("2000000", 0.5)


def test_send_payout_skipped_for_asset_id_not_accepted_on_the_configured_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skips (never guesses/defaults to USDC) when `asset_id` doesn't match any accepted asset on the configured network — e.g. EURQ's mainnet ASA id while pinned to TestNet, where EURQ has no ASA at all."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "kyc_payout_share", 0.5)
    monkeypatch.setattr(
        settings, "x402_network", "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="
    )

    eurq_mainnet_asset_id = str(EURQ.asa_id_for(ALGORAND_MAINNET_CAIP2))
    result = send_payout(receiver=RECEIVER, amount_atomic="2000000", asset_id=eurq_mainnet_asset_id)

    assert result.status == "skipped"
    assert "unrecognized" in (result.error or "").lower()


def test_send_payout_skipped_for_non_numeric_amount_atomic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: `int(amount_atomic)` used to raise ValueError outside its own try/except.

    That contradicted the docstring's "never raises" claim (found 2026-09-03
    while hardening kyc_payout_retry, CLAUDE.md section 9 — this function
    moves real funds).
    """
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "kyc_payout_share", 0.5)

    result = send_payout(
        receiver=RECEIVER, amount_atomic="not-a-number", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert result.status == "skipped"
    assert "not numeric" in (result.error or "")


def test_send_payout_skipped_for_garbage_asset_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips rather than raising when `asset_id` isn't even a parseable integer."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "kyc_payout_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "kyc_payout_share", 0.5)

    result = send_payout(receiver=RECEIVER, amount_atomic="2000000", asset_id="not-an-asa-id")

    assert result.status == "skipped"
    assert "unrecognized" in (result.error or "").lower()
