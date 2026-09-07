"""Auto-refund on product-write failure: send_refund, the circuit breaker, settlement.record_refund, and run_with_refund's wiring contract.

Fully offline: no real Algorand/Redis/Cassandra calls. algod is a fake
client (same pattern as test_kya_payout.py); Redis is a fake at the
get_redis seam (same pattern as test_x402_preview.py/test_x402_promo.py).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Never

import pytest
from algosdk import account, mnemonic
from algosdk.transaction import SuggestedParams
from x402.mechanisms.avm.constants import ALGORAND_MAINNET_CAIP2

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import Response
from app.modules.x402 import circuit_breaker
from app.modules.x402 import refund as refund_module
from app.modules.x402.assets import EURQ, GOBTC, USDC
from app.modules.x402.guard import PaymentResult
from app.modules.x402.paid_request import run_with_refund
from app.modules.x402.refund import send_refund
from app.modules.x402.settlement import InMemorySettlementStore, SettlementRecord, record_refund

_, RECEIVER = account.generate_account()
_DEFAULT_NETWORK_USDC_ASSET_ID = "10458941"


# --------------------------------------------------------------------------- #
# send_refund
# --------------------------------------------------------------------------- #
def test_send_refund_skipped_when_wallet_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips with a "not configured" error when no refund mnemonic is set."""
    monkeypatch.setattr(settings, "x402_refund_mnemonic", "")

    result = send_refund(
        receiver=RECEIVER, amount_atomic="1000000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert result.status == "skipped"
    assert "not configured" in (result.error or "")


def test_send_refund_failed_for_no_receiver(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty/unattributable payer must not silently skip -- there is money to send back and nowhere to send it."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))

    result = send_refund(
        receiver="", amount_atomic="1000000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert result.status == "failed"


def test_send_refund_skipped_for_non_positive_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips rather than sending a zero-value transfer."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))

    result = send_refund(
        receiver=RECEIVER, amount_atomic="0", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert result.status == "skipped"


def test_send_refund_failed_for_garbage_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails rather than raising when amount_atomic isn't a parseable integer."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))

    result = send_refund(
        receiver=RECEIVER, amount_atomic="not-a-number", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert result.status == "failed"


def test_send_refund_skipped_for_unrecognized_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips rather than guessing an asset when asset_id doesn't resolve to an accepted asset."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))

    result = send_refund(receiver=RECEIVER, amount_atomic="1000000", asset_id="not-an-asa-id")

    assert result.status == "skipped"
    assert "unrecognized" in (result.error or "").lower()


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
        return "FAKE_REFUND_TXID"


def test_send_refund_success_sends_the_full_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sends back the FULL amount_atomic -- unlike a payout share, a refund is not split."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(
        settings, "x402_network", "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="
    )
    _patch_breaker_redis(monkeypatch, _FakeRedis())

    sent: list = []
    monkeypatch.setattr(refund_module, "_algod_client", lambda: _FakeAlgodClient(sent))
    monkeypatch.setattr(
        refund_module, "wait_for_confirmation", lambda *_a, **_k: {"confirmed-round": 5}
    )

    result = send_refund(
        receiver=RECEIVER, amount_atomic="2000000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert result.status == "sent"
    assert result.txid == "FAKE_REFUND_TXID"
    assert len(sent) == 1
    assert sent[0].transaction.amount == 2000000  # full amount, not a fraction
    assert sent[0].transaction.index == USDC.asa_id_for(settings.x402_network)


def test_send_refund_confirm_timeout_keeps_the_broadcast_txid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found-in-audit fix (2026-09-02): a broadcast that succeeds but whose confirmation can't be verified must NOT discard the txid and report bare 'failed' -- it was genuinely sent and will very likely land; losing the reference risked a manual double-pay on reconciliation."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(
        settings, "x402_network", "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="
    )
    _patch_breaker_redis(monkeypatch, _FakeRedis())
    sent: list = []
    monkeypatch.setattr(refund_module, "_algod_client", lambda: _FakeAlgodClient(sent))

    def _confirm_times_out(*_a: object, **_kw: object) -> Never:
        raise TimeoutError("confirmation not seen within the wait budget")

    monkeypatch.setattr(refund_module, "wait_for_confirmation", _confirm_times_out)

    result = send_refund(
        receiver=RECEIVER, amount_atomic="2000000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert result.status == "sent_unconfirmed"
    assert result.txid == "FAKE_REFUND_TXID"  # the real, broadcast txid -- never discarded
    assert len(sent) == 1  # the transaction really was broadcast


def test_send_refund_invalid_mnemonic_never_logs_the_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Found-in-audit fix (2026-09-02): the installed algosdk's mnemonic.to_private_key raises ValueError(mnemonic) -- the exception MESSAGE IS THE ENTIRE 25-WORD SECRET -- on a misconfigured mnemonic. That secret must never reach a log line, on this second signing path either."""
    real_secret = "word1 word2 word3 not a real valid mnemonic phrase at all"
    monkeypatch.setattr(settings, "x402_refund_mnemonic", real_secret)
    _patch_breaker_redis(monkeypatch, _FakeRedis())

    with caplog.at_level("DEBUG"):
        result = send_refund(
            receiver=RECEIVER, amount_atomic="1000000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
        )

    assert result.status == "failed"
    assert "mnemonic invalid" in (result.error or "")
    assert real_secret not in (result.error or "")
    for record in caplog.records:
        assert real_secret not in record.getMessage()
        assert "word1" not in record.getMessage()


def test_send_refund_never_raises_on_algod_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns a failed result instead of raising when algod is unreachable."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))
    _patch_breaker_redis(monkeypatch, _FakeRedis())

    def _boom() -> Never:
        raise ConnectionError("algod unreachable")

    monkeypatch.setattr(refund_module, "_algod_client", _boom)

    result = send_refund(
        receiver=RECEIVER, amount_atomic="2000000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert result.status == "failed"
    assert "algod unreachable" in (result.error or "")


# --------------------------------------------------------------------------- #
# settlement.record_refund
# --------------------------------------------------------------------------- #
def test_record_refund_updates_the_ledger_row() -> None:
    """A refund outcome is attached to the settlement row it belongs to, without flipping fulfilled."""
    store = InMemorySettlementStore()
    store.record_settlement(
        SettlementRecord(
            tx_id="TX1",
            asset_id="10458941",
            amount_atomic="1000",
            payer="P" * 58,
            resource="x402-ping",
            network="testnet",
            settled_at_epoch=0,
        )
    )

    ok = record_refund(
        "TX1", refund_tx_id="REFUND1", refund_status="sent", resource="x402-ping", store=store
    )

    assert ok is True
    row = store.get_settlement("TX1")
    assert row is not None
    assert row.refund_tx_id == "REFUND1"
    assert row.refund_status == "sent"
    assert row.fulfilled is False  # a refund never retroactively marks fulfilled


def test_record_refund_returns_false_for_missing_row() -> None:
    """Returns False, never upserts a phantom row, for a txid the ledger never recorded."""
    store = InMemorySettlementStore()

    ok = record_refund(
        "NO-SUCH-TX", refund_tx_id=None, refund_status="failed", resource="x402-ping", store=store
    )

    assert ok is False


def test_record_refund_with_no_txid_never_raises_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing txid logs at ERROR and returns False instead of raising."""
    with caplog.at_level("ERROR"):
        ok = record_refund(None, refund_tx_id=None, refund_status="skipped", resource="x402-ping")

    assert ok is False
    assert any("record_refund called with no settlement txid" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# circuit breaker
# --------------------------------------------------------------------------- #
class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def incrby(self, key: str, amount: int) -> int:
        value = int(self.store.get(key, "0")) + amount
        self.store[key] = str(value)
        return value

    def decrby(self, key: str, amount: int) -> int:
        value = int(self.store.get(key, "0")) - amount
        self.store[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        _ = key, seconds
        return True

    def set(self, key: str, value: str) -> bool:
        self.store[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


class _BrokenRedis:
    def get(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis down")

    def set(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis down")

    def incr(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis down")

    def incrby(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis down")

    def decrby(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis down")

    def delete(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis down")


def _patch_breaker_redis(monkeypatch: pytest.MonkeyPatch, fake: _FakeRedis) -> None:
    """record_refund_failure goes through the shared incr_with_expiry (app.core.rate_limit's own get_redis seam); is_tripped/reset read circuit_breaker's own get_redis directly; send_refund's daily-budget reservation reads refund_module's own get_redis directly. All three need patching -- same one-binding-per-importer convention every x402 module's tests already follow."""
    monkeypatch.setattr(circuit_breaker, "get_redis", lambda: fake)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: fake)
    monkeypatch.setattr(refund_module, "get_redis", lambda: fake)


def test_breaker_is_not_tripped_below_the_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stays closed while the failure count is under the configured max."""
    monkeypatch.setattr(settings, "x402_refund_breaker_max_failures", 5)
    fake = _FakeRedis()
    _patch_breaker_redis(monkeypatch, fake)

    for _ in range(4):
        circuit_breaker.record_refund_failure("x402-ping")

    assert circuit_breaker.is_tripped("x402-ping") is False


def test_breaker_trips_at_the_threshold_and_blocks_further_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trips once the failure count reaches the configured max, within the window."""
    monkeypatch.setattr(settings, "x402_refund_breaker_max_failures", 3)
    fake = _FakeRedis()
    _patch_breaker_redis(monkeypatch, fake)

    for _ in range(3):
        circuit_breaker.record_refund_failure("x402-ping")

    assert circuit_breaker.is_tripped("x402-ping") is True


def test_breaker_trip_is_per_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tripping one resource must not affect another."""
    monkeypatch.setattr(settings, "x402_refund_breaker_max_failures", 2)
    fake = _FakeRedis()
    _patch_breaker_redis(monkeypatch, fake)

    circuit_breaker.record_refund_failure("x402-ping")
    circuit_breaker.record_refund_failure("x402-ping")

    assert circuit_breaker.is_tripped("x402-ping") is True
    assert circuit_breaker.is_tripped("x402-grading-score") is False


def test_breaker_reset_clears_the_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """An admin reset un-trips a resource."""
    monkeypatch.setattr(settings, "x402_refund_breaker_max_failures", 1)
    fake = _FakeRedis()
    _patch_breaker_redis(monkeypatch, fake)
    circuit_breaker.record_refund_failure("x402-ping")
    assert circuit_breaker.is_tripped("x402-ping") is True

    reset_ok = circuit_breaker.reset("x402-ping")

    assert reset_ok is True
    assert circuit_breaker.is_tripped("x402-ping") is False


def test_breaker_stays_tripped_after_the_counter_key_expires_without_a_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found-in-audit regression guard (2026-09-02): a tripped resource must NOT silently un-trip when the rolling-counter key's TTL runs out -- only reset() may clear it.

    The bug this proves fixed: the counter alone (incr_with_expiry, TTL'd to
    the window) is a rate limit, not a latch -- an attacker could trigger
    max_failures refunds, wait out one window, and repeat forever with zero
    admin involvement. Simulating "the counter key expired" directly rather
    than sleeping: delete only the counter key, leave the latch key alone,
    exactly what a real TTL expiry does.
    """
    monkeypatch.setattr(settings, "x402_refund_breaker_max_failures", 2)
    fake = _FakeRedis()
    _patch_breaker_redis(monkeypatch, fake)
    circuit_breaker.record_refund_failure("x402-ping")
    circuit_breaker.record_refund_failure("x402-ping")
    assert circuit_breaker.is_tripped("x402-ping") is True

    # Simulate the rolling counter's TTL expiring -- delete ONLY that key,
    # not the latch, same state a real Redis expiry would leave behind.
    del fake.store["algorand:x402:refund_breaker:x402-ping"]

    assert circuit_breaker.is_tripped("x402-ping") is True

    # Only an explicit reset actually clears it.
    assert circuit_breaker.reset("x402-ping") is True
    assert circuit_breaker.is_tripped("x402-ping") is False


def test_daily_budget_exhausted_skips_the_refund_without_sending_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found-in-audit fix (2026-09-02): a marketplace-wide per-asset daily ceiling -- past it, send_refund skips (no funds move) instead of sending unboundedly."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "x402_refund_daily_budget_usd_atomic", 1_000_000)
    _patch_breaker_redis(monkeypatch, _FakeRedis())
    sent: list = []
    monkeypatch.setattr(refund_module, "_algod_client", lambda: _FakeAlgodClient(sent))
    monkeypatch.setattr(
        refund_module, "wait_for_confirmation", lambda *_a, **_k: {"confirmed-round": 5}
    )

    first = send_refund(
        receiver=RECEIVER, amount_atomic="900000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )
    second = send_refund(
        receiver=RECEIVER, amount_atomic="900000", asset_id=_DEFAULT_NETWORK_USDC_ASSET_ID
    )

    assert first.status == "sent"
    assert second.status == "skipped"
    assert "budget" in (second.error or "")
    assert len(sent) == 1  # the second refund never built or sent a transaction


def test_daily_budget_is_per_asset_not_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    """A different asset gets its own independent budget -- exhausting USDC's must not block EURQ's.

    EURQ has no TestNet ASA (app.modules.x402.assets' own docstring), so
    this runs on MainNet -- the real, verified EURQ asa id (assets.py).
    """
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "x402_network", ALGORAND_MAINNET_CAIP2)
    monkeypatch.setattr(settings, "x402_refund_daily_budget_usd_atomic", 1_000_000)
    # EURQ is priced via price_oracle (it has a coingecko_id, unlike USDC) --
    # pin its rate to exactly 1.0 so this test's assertions stay about
    # per-asset budget isolation, not price normalization math.
    monkeypatch.setattr(refund_module, "get_usd_rate", lambda _coingecko_id: Decimal("1.0"))
    _patch_breaker_redis(monkeypatch, _FakeRedis())
    sent: list = []
    monkeypatch.setattr(refund_module, "_algod_client", lambda: _FakeAlgodClient(sent))
    monkeypatch.setattr(
        refund_module, "wait_for_confirmation", lambda *_a, **_k: {"confirmed-round": 5}
    )
    usdc_mainnet_asa_id = str(USDC.asa_id_for(ALGORAND_MAINNET_CAIP2))
    eurq_mainnet_asa_id = str(EURQ.asa_id_for(ALGORAND_MAINNET_CAIP2))

    exhausted = send_refund(
        receiver=RECEIVER, amount_atomic="2000000", asset_id=usdc_mainnet_asa_id
    )
    other_asset = send_refund(
        receiver=RECEIVER, amount_atomic="900000", asset_id=eurq_mainnet_asa_id
    )

    assert exhausted.status == "skipped"
    assert other_asset.status == "sent"


def test_goBTC_and_usdc_refunds_of_equal_real_value_consume_equal_budget_fraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the 2026-09-06 incident: goBTC (8 decimals, BTC-priced) must consume the SAME fraction of its OWN daily budget as USDC (6 decimals, ~$1) does of ITS OWN, for genuinely equivalent real-world value -- not a fraction 100x-1000x smaller, which is what happened with the old flat-atomic-number budget.

    The budget stays tracked PER ASSET (test_daily_budget_is_per_asset_not_shared
    above), so this proves the fraction-of-budget-consumed is comparable
    per asset, by driving each asset's own $1.00 budget to exhaustion with
    two genuinely-$0.50 refunds and checking a third is refused either way.
    goBTC is pinned to $50,000/BTC: $0.50 of goBTC is 0.00001 BTC = 1_000
    atomic units (8 decimals); $0.50 of USDC is 500_000 atomic units (6
    decimals) directly, since USDC needs no oracle. Nothing here
    special-cases goBTC by name, only by its .decimals/.coingecko_id, so
    this generalizes to a future asset with yet another decimals value.
    """
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "x402_network", ALGORAND_MAINNET_CAIP2)
    monkeypatch.setattr(settings, "x402_refund_daily_budget_usd_atomic", 1_000_000)  # $1.00
    monkeypatch.setattr(
        refund_module,
        "get_usd_rate",
        lambda coingecko_id: Decimal("50000") if coingecko_id == GOBTC.coingecko_id else None,
    )
    _patch_breaker_redis(monkeypatch, _FakeRedis())
    sent: list = []
    monkeypatch.setattr(refund_module, "_algod_client", lambda: _FakeAlgodClient(sent))
    monkeypatch.setattr(
        refund_module, "wait_for_confirmation", lambda *_a, **_k: {"confirmed-round": 5}
    )
    gobtc_mainnet_asa_id = str(GOBTC.asa_id_for(ALGORAND_MAINNET_CAIP2))
    usdc_mainnet_asa_id = str(USDC.asa_id_for(ALGORAND_MAINNET_CAIP2))

    gobtc_first_half = send_refund(  # $0.50 of goBTC -- 50% of its $1.00 budget
        receiver=RECEIVER, amount_atomic="1000", asset_id=gobtc_mainnet_asa_id
    )
    gobtc_second_half = send_refund(  # the other 50% -- exhausts goBTC's budget
        receiver=RECEIVER, amount_atomic="1000", asset_id=gobtc_mainnet_asa_id
    )
    gobtc_third = send_refund(  # goBTC's own budget is now fully consumed
        receiver=RECEIVER, amount_atomic="1", asset_id=gobtc_mainnet_asa_id
    )
    usdc_first_half = send_refund(  # $0.50 of USDC -- 50% of ITS $1.00 budget
        receiver=RECEIVER, amount_atomic="500000", asset_id=usdc_mainnet_asa_id
    )
    usdc_second_half = send_refund(  # the other 50% -- exhausts USDC's budget
        receiver=RECEIVER, amount_atomic="500000", asset_id=usdc_mainnet_asa_id
    )
    usdc_third = send_refund(  # USDC's own budget is now fully consumed too
        receiver=RECEIVER, amount_atomic="1", asset_id=usdc_mainnet_asa_id
    )

    assert [r.status for r in (gobtc_first_half, gobtc_second_half)] == ["sent", "sent"]
    assert gobtc_third.status == "skipped"
    assert [r.status for r in (usdc_first_half, usdc_second_half)] == ["sent", "sent"]
    assert usdc_third.status == "skipped"


def test_gobtc_refund_of_the_old_flat_atomic_budget_number_would_have_blown_the_real_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct regression on the incident: under the OLD code, sending the full 100_000_000-atomic budget number in goBTC (1.0 whole goBTC) was allowed -- worth ~$50,000 here, not the intended ~$100. The fix must refuse that same 1.0 goBTC refund as WAY over budget while still allowing a genuinely ~$100-equivalent goBTC refund through."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "x402_network", ALGORAND_MAINNET_CAIP2)
    # Use the real default budget: $100/day, unchanged in atomic terms.
    assert settings.x402_refund_daily_budget_usd_atomic == 100_000_000
    monkeypatch.setattr(
        refund_module,
        "get_usd_rate",
        lambda coingecko_id: Decimal("50000") if coingecko_id == GOBTC.coingecko_id else None,
    )
    _patch_breaker_redis(monkeypatch, _FakeRedis())
    sent: list = []
    monkeypatch.setattr(refund_module, "_algod_client", lambda: _FakeAlgodClient(sent))
    monkeypatch.setattr(
        refund_module, "wait_for_confirmation", lambda *_a, **_k: {"confirmed-round": 5}
    )
    gobtc_mainnet_asa_id = str(GOBTC.asa_id_for(ALGORAND_MAINNET_CAIP2))

    # The exact atomic number the OLD buggy config used as its (asset-blind)
    # budget: 100_000_000 atomic of an 8-decimal asset is 1.0 whole goBTC,
    # worth ~$50,000 at the pinned rate -- 500x the intended ~$100 ceiling.
    one_whole_gobtc = send_refund(
        receiver=RECEIVER, amount_atomic="100000000", asset_id=gobtc_mainnet_asa_id
    )
    # A genuinely ~$100-equivalent goBTC refund (0.002 BTC @ $50,000) must
    # still go through on a fresh day's budget.
    monkeypatch.setattr(refund_module, "_daily_budget_key", lambda asset_id: f"fresh:{asset_id}")
    genuinely_100_dollars = send_refund(
        receiver=RECEIVER, amount_atomic="200000", asset_id=gobtc_mainnet_asa_id
    )

    assert one_whole_gobtc.status == "skipped"
    assert one_whole_gobtc.error == "daily refund budget exhausted"
    assert genuinely_100_dollars.status == "sent"
    assert len(sent) == 1  # only the genuinely-$100 refund was ever broadcast


def test_daily_budget_check_fails_closed_when_gobtc_price_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A priced (non-USDC) asset with no available rate right now must skip the refund, not sail through the budget check unenforced -- fails CLOSED, same reasoning as an unreachable Redis."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(settings, "x402_network", ALGORAND_MAINNET_CAIP2)
    monkeypatch.setattr(refund_module, "get_usd_rate", lambda _coingecko_id: None)
    _patch_breaker_redis(monkeypatch, _FakeRedis())
    gobtc_mainnet_asa_id = str(GOBTC.asa_id_for(ALGORAND_MAINNET_CAIP2))

    result = send_refund(receiver=RECEIVER, amount_atomic="1000", asset_id=gobtc_mainnet_asa_id)

    assert result.status == "skipped"
    assert result.error == "daily refund budget exhausted"


def test_breaker_fails_closed_on_redis_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike this codebase's usual free-endpoint budgets (fail open), the refund breaker's own check fails CLOSED -- an unreachable counter must refuse, not allow."""
    monkeypatch.setattr(circuit_breaker, "get_redis", lambda: _BrokenRedis())

    assert circuit_breaker.is_tripped("x402-ping") is True


# --------------------------------------------------------------------------- #
# run_with_refund
# --------------------------------------------------------------------------- #
def _settled_result(*, payer: str = RECEIVER, txid: str = "TX1") -> PaymentResult:
    return PaymentResult(
        error=None,
        payer=payer,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="1000",
        payment_txid=txid,
        asset_id="10458941",
        network="algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
    )


def test_run_with_refund_returns_the_product_write_result_on_success() -> None:
    """A successful product_write's return value passes through untouched, no refund attempted."""
    result = _settled_result()
    store = InMemorySettlementStore()
    store.record_settlement(
        SettlementRecord(
            tx_id="TX1",
            asset_id="10458941",
            amount_atomic="1000",
            payer=result.payer or "",
            resource="x402-ping",
            network="testnet",
            settled_at_epoch=0,
        )
    )

    outcome = run_with_refund(
        result,
        resource="x402-ping",
        product_write=lambda: {"pong": True},
        settlement_store=store,
    )

    assert outcome == {"pong": True}
    # No refund attempted on the success path -- the ledger row is untouched.
    row = store.get_settlement("TX1")
    assert row is not None
    assert row.refund_status is None


def test_run_with_refund_refunds_and_returns_a_response_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising product_write triggers a real refund, records it on the ledger, and returns a 503 instead of propagating."""
    priv, _ = account.generate_account()
    monkeypatch.setattr(settings, "x402_refund_mnemonic", mnemonic.from_private_key(priv))
    monkeypatch.setattr(
        settings, "x402_network", "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="
    )
    sent: list = []
    monkeypatch.setattr(refund_module, "_algod_client", lambda: _FakeAlgodClient(sent))
    monkeypatch.setattr(
        refund_module, "wait_for_confirmation", lambda *_a, **_k: {"confirmed-round": 5}
    )
    fake_redis = _FakeRedis()
    _patch_breaker_redis(monkeypatch, fake_redis)

    result = _settled_result(txid="TX2")
    store = InMemorySettlementStore()
    store.record_settlement(
        SettlementRecord(
            tx_id="TX2",
            asset_id="10458941",
            amount_atomic="1000",
            payer=result.payer or "",
            resource="x402-ping",
            network="testnet",
            settled_at_epoch=0,
        )
    )

    def _boom() -> Never:
        raise RuntimeError("the product write blew up")

    outcome = run_with_refund(
        result, resource="x402-ping", product_write=_boom, settlement_store=store
    )

    assert isinstance(outcome, Response)
    assert outcome.status_code == 503
    assert "refunded" in outcome.description.lower()
    assert len(sent) == 1  # a real refund transaction was actually built and sent

    row = store.get_settlement("TX2")
    assert row is not None
    assert row.refund_status == "sent"
    assert row.refund_tx_id == "FAKE_REFUND_TXID"
    assert row.fulfilled is False

    # The circuit breaker counted this failure.
    assert circuit_breaker.is_tripped("x402-ping") is False  # below the default threshold (5)
    assert fake_redis.store.get("algorand:x402:refund_breaker:x402-ping") == "1"


def test_run_with_refund_still_returns_a_response_when_the_refund_itself_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refund wallet unreachable: the caller still gets an honest response, and the ledger row stays reconcilable, never silently lost."""
    monkeypatch.setattr(settings, "x402_refund_mnemonic", "")  # unconfigured -> "skipped"
    fake_redis = _FakeRedis()
    _patch_breaker_redis(monkeypatch, fake_redis)

    result = _settled_result(txid="TX3")
    store = InMemorySettlementStore()
    store.record_settlement(
        SettlementRecord(
            tx_id="TX3",
            asset_id="10458941",
            amount_atomic="1000",
            payer=result.payer or "",
            resource="x402-ping",
            network="testnet",
            settled_at_epoch=0,
        )
    )

    def _boom() -> Never:
        raise RuntimeError("boom")

    outcome = run_with_refund(
        result, resource="x402-ping", product_write=_boom, settlement_store=store
    )

    assert isinstance(outcome, Response)
    assert outcome.status_code == 503
    assert "refund_pending" in outcome.description or "pending" in outcome.description.lower()

    row = store.get_settlement("TX3")
    assert row is not None
    assert row.refund_status == "skipped"  # visible for manual reconciliation
    assert row.fulfilled is False
