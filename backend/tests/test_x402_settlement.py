"""x402 settlement ledger tests: EUR valuation and fulfillment tracking.

Fully offline. The price oracle is faked at its own `get_eur_rate` seam in
settlement.py, the ledger is the in-memory store, and the Cassandra store is
driven against a recording fake session so the exact statement/parameter
shape of the store-before-mark sequence is pinned.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Never

import pytest

pytest.importorskip("x402")

from x402.mechanisms.avm.constants import (
    ALGORAND_MAINNET_CAIP2,
    ALGORAND_TESTNET_CAIP2,
    USDC_TESTNET_ASA_ID,
)

from app.core.statements import X402Stmts
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import settlement
from app.modules.x402.settlement import (
    EUR_VALUE_UNAVAILABLE,
    CassandraSettlementStore,
    InMemorySettlementStore,
    SettlementRecord,
    recent_real_settlements,
)

_PAYER = "P" * 58


def _settled_result(
    *,
    txid: str = "TX123",
    asset_id: str = str(USDC_TESTNET_ASA_ID),
    amount_atomic: str = "100000",
    network: str = ALGORAND_TESTNET_CAIP2,
) -> x402_guard.PaymentResult:
    return x402_guard.PaymentResult(
        error=None,
        payer=_PAYER,
        amount_atomic=amount_atomic,
        payment_txid=txid,
        asset_id=asset_id,
        network=network,
    )


@pytest.fixture
def eur_rates(monkeypatch: pytest.MonkeyPatch) -> dict[str, Decimal]:
    """Fake the EUR oracle seam: {coingecko id: EUR per unit}. Missing id == no rate."""
    rates: dict[str, Decimal] = {}
    monkeypatch.setattr(settlement, "get_eur_rate", lambda asset: rates.get(asset))
    return rates


# --------------------------------------------------------------------------- #
# Fix 1: EUR value at settlement time
# --------------------------------------------------------------------------- #
def test_eur_value_is_computed_from_the_oracle_rate_and_asset_decimals(
    eur_rates: dict[str, Decimal],
) -> None:
    """0.10 USDC at 0.92 EUR/USDC is recorded as 0.092 EUR, using the asset's 6 decimals."""
    eur_rates["usd-coin"] = Decimal("0.92")
    store = InMemorySettlementStore()

    settlement.record_settlement(_settled_result(), resource="r", store=store)

    assert store.settlements[0].eur_value == pytest.approx(0.092)


def test_eur_value_for_a_non_usdc_asset_uses_its_own_coingecko_id(
    eur_rates: dict[str, Decimal],
) -> None:
    """A EURQ payment is priced under quantoz-eurq, not under USDC's id."""
    eur_rates["quantoz-eurq"] = Decimal("1.0")
    eur_rates["usd-coin"] = Decimal("0.5")  # must NOT be used
    store = InMemorySettlementStore()

    settlement.record_settlement(
        _settled_result(
            asset_id="2768422954", amount_atomic="2500000", network=ALGORAND_MAINNET_CAIP2
        ),
        resource="r",
        store=store,
    )

    assert store.settlements[0].eur_value == pytest.approx(2.5)


def test_missing_rate_records_the_unavailable_sentinel_not_zero(
    eur_rates: dict[str, Decimal], caplog: pytest.LogCaptureFixture
) -> None:
    """With no EUR rate the ledger row says "unavailable" (-1.0), never 0.0, and warns."""
    del eur_rates  # empty: no rate for anything
    store = InMemorySettlementStore()

    with caplog.at_level("WARNING"):
        settlement.record_settlement(_settled_result(), resource="r", store=store)

    assert store.settlements[0].eur_value == EUR_VALUE_UNAVAILABLE
    assert store.settlements[0].eur_value < 0
    assert "eur_value as unavailable" in caplog.text


@pytest.mark.usefixtures("eur_rates")
def test_unknown_asset_or_bad_amount_records_the_sentinel() -> None:
    """An asset_id no accepted asset names, or a non-integer amount, cannot be priced and says so."""
    store = InMemorySettlementStore()

    settlement.record_settlement(_settled_result(asset_id="999999"), resource="r", store=store)
    settlement.record_settlement(_settled_result(amount_atomic="lots"), resource="r", store=store)

    assert [s.eur_value for s in store.settlements] == [EUR_VALUE_UNAVAILABLE] * 2


def test_an_oracle_exception_never_blocks_the_settlement_write(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A raising oracle is contained: the row is still written, with the sentinel, and a warning."""

    def _boom(_asset: str) -> Never:
        raise RuntimeError("coingecko exploded")

    monkeypatch.setattr(settlement, "get_eur_rate", _boom)
    store = InMemorySettlementStore()

    with caplog.at_level("WARNING"):
        settlement.record_settlement(_settled_result(), resource="r", store=store)

    assert len(store.settlements) == 1
    assert store.settlements[0].eur_value == EUR_VALUE_UNAVAILABLE
    assert "EUR rate lookup raised" in caplog.text


# --------------------------------------------------------------------------- #
# Fix 2: fulfillment tracking
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("eur_rates")
def test_a_settlement_is_recorded_unfulfilled_until_the_route_marks_it() -> None:
    """The ledger row is written fulfilled=False; mark_fulfilled flips it after the product write."""
    store = InMemorySettlementStore()
    settlement.record_settlement(_settled_result(), resource="r", store=store)
    assert store.get_settlement("TX123") is not None
    assert store.get_settlement("TX123").fulfilled is False  # type: ignore[union-attr]

    assert settlement.mark_fulfilled("TX123", resource="r", store=store) is True

    assert store.get_settlement("TX123").fulfilled is True  # type: ignore[union-attr]
    # Idempotent: a second mark is a no-op success, never a second fulfillment.
    assert settlement.mark_fulfilled("TX123", resource="r", store=store) is True


@pytest.mark.usefixtures("eur_rates")
def test_a_failed_product_write_leaves_a_durable_unfulfilled_row() -> None:
    """Regression for the review finding: if the route's write raises, the ledger still says fulfilled=False."""
    store = InMemorySettlementStore()
    result = _settled_result()
    settlement.record_settlement(result, resource="r", store=store)

    def _route_body() -> None:
        raise RuntimeError("cassandra down mid-write")
        settlement.mark_fulfilled(result.payment_txid, resource="r", store=store)  # never reached

    with pytest.raises(RuntimeError):
        _route_body()

    row = store.get_settlement("TX123")
    assert row is not None
    assert row.fulfilled is False
    assert row.payer == _PAYER


def test_mark_fulfilled_with_no_ledger_row_logs_and_returns_false(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A txid the ledger never recorded (its write failed earlier) is reported, not upserted."""
    store = InMemorySettlementStore()

    with caplog.at_level("ERROR"):
        assert settlement.mark_fulfilled("NOPE", resource="r", store=store) is False
        assert settlement.mark_fulfilled(None, resource="r", store=store) is False

    assert "found no ledger row for tx_id=NOPE" in caplog.text
    assert "no settlement txid" in caplog.text
    assert store.settlements == []


def test_mark_fulfilled_never_raises_when_the_store_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The product is already stored, so a failing mark logs at ERROR with the txid and returns False."""

    class _BrokenStore(InMemorySettlementStore):
        def mark_fulfilled(self, tx_id: str) -> Never:  # noqa: ARG002 -- signature must match the Protocol
            raise RuntimeError("cassandra down")

    with caplog.at_level("ERROR"):
        assert settlement.mark_fulfilled("TX123", resource="r", store=_BrokenStore()) is False

    assert "FULFILLMENT MARK FAILED" in caplog.text
    assert "TX123" in caplog.text


# --------------------------------------------------------------------------- #
# Cassandra store: exact statement sequence
# --------------------------------------------------------------------------- #
def _cql(name: str) -> str:
    """The raw CQL behind one X402Stmts entry, bypassing the prepare-on-access descriptor."""
    return X402Stmts.__dict__[name].cql


class _FakeSession:
    """Records every execute as (cql, params); answers GET_SETTLEMENT_BY_TX from a canned row."""

    def __init__(self, by_tx_row: SimpleNamespace | None = None) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.by_tx_row = by_tx_row

    def prepare(self, cql: str) -> SimpleNamespace:
        return SimpleNamespace(cql=cql)

    def execute(self, stmt: SimpleNamespace, params: tuple[object, ...]) -> SimpleNamespace:
        self.calls.append((stmt.cql, params))
        if stmt.cql == _cql("GET_SETTLEMENT_BY_TX"):
            return SimpleNamespace(one=lambda: self.by_tx_row)
        return SimpleNamespace(one=lambda: None)


def _cassandra_store(
    monkeypatch: pytest.MonkeyPatch, session: _FakeSession
) -> CassandraSettlementStore:
    import app.core.cassandra as cassandra_core

    # prepare_cached is process-wide and keyed by CQL; clear it so this
    # session's prepare is the one that answers.
    cassandra_core.prepare_cached.cache_clear()
    monkeypatch.setattr(cassandra_core, "get_cassandra_session", lambda: session)
    return CassandraSettlementStore()


def test_cassandra_record_writes_ledger_row_then_by_tx_row_with_fulfilled_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full INSERTs, ledger first then the lookup, both carrying fulfilled=False and the EUR value."""
    session = _FakeSession()
    store = _cassandra_store(monkeypatch, session)
    epoch = int(datetime(2026, 8, 30, 12, 0, tzinfo=UTC).timestamp())

    store.record_settlement(
        SettlementRecord(
            tx_id="TX1",
            asset_id="10458941",
            amount_atomic="100000",
            payer=_PAYER,
            resource="r",
            network=ALGORAND_TESTNET_CAIP2,
            settled_at_epoch=epoch,
            eur_value=0.092,
        )
    )

    assert [c[0] for c in session.calls] == [
        _cql("INSERT_SETTLEMENT"),
        _cql("INSERT_SETTLEMENT_BY_TX"),
    ]
    ledger_params = session.calls[0][1]
    assert ledger_params[0] == "2026-08-30"
    assert ledger_params[2] == "TX1"
    # eur_value, fulfilled, refund_tx_id, refund_status -- the latter two
    # always None at insert time, a refund is only ever attempted later.
    assert ledger_params[-4:] == (0.092, False, None, None)
    by_tx_params = session.calls[1][1]
    assert by_tx_params[:2] == ("TX1", "2026-08-30")
    assert by_tx_params[-4:] == (0.092, False, None, None)


def test_cassandra_mark_fulfilled_reads_the_key_first_and_never_upserts_a_phantom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a by-tx hit both tables are updated on the exact key; with none, nothing is written."""
    settled_at = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    hit = _FakeSession(by_tx_row=SimpleNamespace(day="2026-08-30", settled_at=settled_at))
    assert _cassandra_store(monkeypatch, hit).mark_fulfilled("TX1") is True
    assert [c[0] for c in hit.calls] == [
        _cql("GET_SETTLEMENT_BY_TX"),
        _cql("MARK_SETTLEMENT_FULFILLED"),
        _cql("MARK_SETTLEMENT_BY_TX_FULFILLED"),
    ]
    assert hit.calls[1][1] == (True, "2026-08-30", settled_at, "TX1")
    assert hit.calls[2][1] == (True, "TX1")

    miss = _FakeSession(by_tx_row=None)
    assert _cassandra_store(monkeypatch, miss).mark_fulfilled("TX1") is False
    assert [c[0] for c in miss.calls] == [_cql("GET_SETTLEMENT_BY_TX")]


def _record(*, tx_id: str, payer: str, settled_at_epoch: int) -> SettlementRecord:
    """A minimal settled, fulfilled record for the recent-settlements tests."""
    return SettlementRecord(
        tx_id=tx_id,
        asset_id="31566704",
        amount_atomic="10000",
        payer=payer,
        resource="x402-directory-list",
        network=ALGORAND_MAINNET_CAIP2,
        settled_at_epoch=settled_at_epoch,
        eur_value=0.09,
        fulfilled=True,
    )


def test_recent_real_settlements_excludes_configured_probe_payers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settlement from an X402_PROBE_PAYERS wallet never appears in the real feed."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "x402_probe_payers", _PAYER)
    store = InMemorySettlementStore()
    now = int(datetime.now(tz=UTC).timestamp())
    store.record_settlement(_record(tx_id="probe-tx", payer=_PAYER, settled_at_epoch=now))
    store.record_settlement(_record(tx_id="real-tx", payer="Q" * 58, settled_at_epoch=now - 1))

    result = recent_real_settlements(store=store)

    assert [r.tx_id for r in result] == ["real-tx"]


def test_recent_real_settlements_is_newest_first_and_bounded() -> None:
    """Results come back newest-first and respect the limit."""
    store = InMemorySettlementStore()
    now = int(datetime.now(tz=UTC).timestamp())
    for i in range(5):
        store.record_settlement(_record(tx_id=f"tx-{i}", payer="Q" * 58, settled_at_epoch=now - i))

    result = recent_real_settlements(store=store, limit=3)

    assert [r.tx_id for r in result] == ["tx-0", "tx-1", "tx-2"]


def test_recent_real_settlements_looks_back_across_empty_days() -> None:
    """A settlement from yesterday is still found when today's partition is empty."""
    store = InMemorySettlementStore()
    yesterday = int(datetime.now(tz=UTC).timestamp()) - 86400
    store.record_settlement(
        _record(tx_id="yesterday-tx", payer="Q" * 58, settled_at_epoch=yesterday)
    )

    result = recent_real_settlements(store=store, lookback_days=3)

    assert [r.tx_id for r in result] == ["yesterday-tx"]


def test_cassandra_list_for_day_reverses_the_ascending_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Cassandra store's page comes back stored-order-ASC; list_for_day serves it newest-first."""
    rows = [
        SimpleNamespace(
            settled_at=datetime.fromtimestamp(1000 + i, tz=UTC),
            tx_id=f"tx-{i}",
            asset_id="31566704",
            amount_atomic="10000",
            payer="Q" * 58,
            resource="x402-directory-list",
            network=ALGORAND_MAINNET_CAIP2,
            eur_value=0.09,
            fulfilled=True,
            refund_tx_id=None,
            refund_status=None,
        )
        for i in range(3)
    ]

    class _ListSession(_FakeSession):
        def execute(self, stmt: SimpleNamespace, params: tuple[object, ...]) -> SimpleNamespace:
            self.calls.append((stmt.cql, params))
            assert stmt.cql == _cql("LIST_SETTLEMENTS_FOR_DAY_FULL")
            assert params == ("2026-08-30", 200)
            return rows

    store = _cassandra_store(monkeypatch, _ListSession())

    result = store.list_for_day("2026-08-30", limit=200)

    assert [r.tx_id for r in result] == ["tx-2", "tx-1", "tx-0"]
