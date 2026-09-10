"""Promo-code tests for the shared x402 payment gate (modules/x402/promo.py).

Fully offline: Redis is a fake at the get_redis seam, the Cassandra-backed
store is exercised via its own get_cassandra_session/prepare_cached seam
(see _patch_cassandra below), and the promo store used by the redemption-path
tests is the module's own in-memory backend. Nothing here settles a real
payment or writes to the settlement ledger — a promo redemption is never a
payment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Never
from unittest.mock import MagicMock

import pytest
from algosdk.encoding import encode_address
from conftest import execute_pairs

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request
from app.core.http_errors import json_error_response
from app.modules.x402 import circuit_breaker
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import promo as promo_module
from app.modules.x402.promo import (
    CassandraPromoStore,
    InMemoryPromoStore,
    PromoError,
    PromoRecord,
    RedemptionRecord,
)
from app.modules.x402_catalog.api import routes as catalog_routes

_WALLET = encode_address(bytes([1]) + bytes(31))
_WALLET_2 = encode_address(bytes([2]) + bytes(31))
_RESOURCE = "x402-scan-url"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Enough of the Redis API for the promo remaining-count DECR and the attempt rate limiter."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(
        self, key: str, value: object, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        _ = ex
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def decr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) - 1
        self.store[key] = str(value)
        return value

    def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    def expire(self, key: str, seconds: int) -> bool:
        _ = key, seconds
        return True


class _BrokenRedis:
    """Every operation fails, to exercise the fail-CLOSED redemption path."""

    def set(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def get(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def incr(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def decr(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def expire(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")


def _request(
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    query: dict[str, Any] | None = None,
    body: bytes = b"",
    path: str = "/api/v1/x402/scan/url",
) -> Request:
    return Request(
        method=method,
        headers=headers or {},
        query_params=QueryParams(query or {}),
        path_params={},
        body=body,
        url=SimpleNamespace(scheme="http", host="localhost", path=path),
    )


@pytest.fixture
def promo_store() -> InMemoryPromoStore:
    """A fresh in-memory promo store per test."""
    return InMemoryPromoStore()


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Swap the Redis seams the promo remaining-count DECR and the attempt rate limiter read."""
    client = _FakeRedis()
    monkeypatch.setattr(promo_module, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
    # ping's mandatory pre-gate circuit-breaker check reads its own get_redis
    # seam directly (app.modules.x402.circuit_breaker) -- see
    # test_x402_preview.py's fake_redis for the same fix and why it's needed.
    monkeypatch.setattr(circuit_breaker, "get_redis", lambda **_kw: client)
    return client


def _patch_cassandra(
    monkeypatch: pytest.MonkeyPatch, session: MagicMock | None = None
) -> MagicMock:
    """Patch promo.py's own get_cassandra_session + the shared prepare_cached.

    promo.py imports get_cassandra_session at module top (CLAUDE.md section 3:
    no new function-local import as a DI seam), so the seam is the module's
    own bound name, not conftest.patch_cassandra (which patches app.core.cassandra's attribute
    directly and only reaches code that re-imports it per call).
    """
    if session is None:
        session = MagicMock()
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(promo_module, "get_cassandra_session", lambda: session)
    return session


def _seed(
    store: InMemoryPromoStore,
    *,
    code: str = "LAUNCH",
    resource: str = _RESOURCE,
    starting_count: int = 5,
    expires_at_epoch: int = 0,
    active: bool = True,
    max_redemptions_per_wallet: int = 1,
) -> None:
    store.create_code(
        PromoRecord(
            code=code,
            resource=resource,
            starting_count=starting_count,
            created_at_epoch=1000,
            expires_at_epoch=expires_at_epoch,
            active=active,
            max_redemptions_per_wallet=max_redemptions_per_wallet,
        )
    )


# --------------------------------------------------------------------------- #
# promo_request_params: the query-param trigger
# --------------------------------------------------------------------------- #
def test_promo_request_params_reads_both_query_params() -> None:
    """?promo= and ?promo_wallet= are both read and trimmed."""
    code, wallet = promo_module.promo_request_params(
        _request(query={"promo": " LAUNCH ", "promo_wallet": f" {_WALLET} "})
    )
    assert (code, wallet) == ("LAUNCH", _WALLET)


def test_promo_request_params_defaults_to_empty() -> None:
    """No promo query params at all reads back as ("", "") -- no promo attempted."""
    assert promo_module.promo_request_params(_request()) == ("", "")


# --------------------------------------------------------------------------- #
# attempt_promo_redemption: the redemption path
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_valid_code_and_wallet_bypasses_payment(promo_store: InMemoryPromoStore) -> None:
    """A valid code + syntactically valid wallet bypasses payment with is_promo=True and nothing settled."""
    _seed(promo_store)

    result = promo_module.attempt_promo_redemption(
        _request(headers={"x-real-ip": "203.0.113.1"}),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )

    assert result is not None
    assert result.error is None
    assert result.is_promo is True
    assert result.is_preview is False
    # Nothing settled: no txid, no headers -- there is no payment. `payer` IS
    # set, though, to the caller-supplied `wallet`, so a route whose product
    # write needs an identity even on a promo bypass never sees payer=None.
    # `wallet` here is only syntactically validated
    # (is_valid_address, no signature proof) -- same caveat the module
    # docstring's own "Wallet abuse guard" section already documents for
    # every other use of this value, now also true of the identity a promo
    # call attributes work to.
    assert result.payment_txid is None
    assert result.payer == _WALLET
    assert result.settlement_headers == {}


def test_exhausted_code_falls_through_to_normal_payment(
    promo_store: InMemoryPromoStore, fake_redis: _FakeRedis, caplog: pytest.LogCaptureFixture
) -> None:
    """A code whose remaining count hits zero falls through to the normal paid gate, not an error.

    Root-caused 2026-09-03 (operator-clarity follow-up): this was completely silent server-side
    too -- an admin/support agent had no way to distinguish "exhausted" from every other silent
    rejection reason without reading Redis directly. Now logs an INFO line naming it explicitly,
    still without changing the caller-facing contract (still a plain fall-through, never a 402 the
    caller can't route around -- see the module docstring's owner decision).
    """
    _seed(promo_store, starting_count=1)

    with caplog.at_level("INFO", logger="app.modules.x402.promo"):
        first = promo_module.attempt_promo_redemption(
            _request(headers={"x-real-ip": "1.1.1.1"}),
            code="LAUNCH",
            wallet=_WALLET,
            resource=_RESOURCE,
            store=promo_store,
        )
        second = promo_module.attempt_promo_redemption(
            _request(headers={"x-real-ip": "1.1.1.2"}),
            code="LAUNCH",
            wallet=_WALLET_2,
            resource=_RESOURCE,
            store=promo_store,
        )

    assert first is not None
    assert second is None  # falls through, not an error
    # The exhausted decrement was undone -- remaining stays at 0, not -1.
    assert fake_redis.store[f"{promo_module._REMAINING_PREFIX}LAUNCH"] == "0"
    assert any(
        "code=LAUNCH exhausted (1/1 uses spent)" in record.message for record in caplog.records
    )


def test_wrong_resource_scope_falls_through_without_touching_redis(
    promo_store: InMemoryPromoStore, fake_redis: _FakeRedis
) -> None:
    """A code scoped to a different resource falls through before Redis is ever touched."""
    _seed(promo_store, resource=_RESOURCE)

    result = promo_module.attempt_promo_redemption(
        _request(headers={"x-real-ip": "1.1.1.1"}),
        code="LAUNCH",
        wallet=_WALLET,
        resource="x402-grading-score",
        store=promo_store,
    )

    assert result is None
    assert f"{promo_module._REMAINING_PREFIX}LAUNCH" not in fake_redis.store


def test_same_code_and_wallet_twice_is_refused_the_second_time(
    promo_store: InMemoryPromoStore, fake_redis: _FakeRedis
) -> None:
    """The same (code, wallet) pair redeems once; the second attempt falls through and its reserved slot is given back."""
    _seed(promo_store, starting_count=5)
    headers = {"x-real-ip": "1.1.1.1"}

    first = promo_module.attempt_promo_redemption(
        _request(headers=headers),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )
    second = promo_module.attempt_promo_redemption(
        _request(headers=headers),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )

    assert first is not None
    assert second is None  # falls through, does not error
    # The reserved slot from the refused second attempt was given back.
    assert fake_redis.store[f"{promo_module._REMAINING_PREFIX}LAUNCH"] == "4"


def test_wallet_redeems_up_to_its_per_wallet_cap_then_falls_through(
    promo_store: InMemoryPromoStore, fake_redis: _FakeRedis
) -> None:
    """max_redemptions_per_wallet=4 lets the SAME wallet succeed 4 times, then falls through on the 5th."""
    _seed(promo_store, starting_count=25, max_redemptions_per_wallet=4)
    headers = {"x-real-ip": "1.1.1.1"}

    results = [
        promo_module.attempt_promo_redemption(
            _request(headers=headers),
            code="LAUNCH",
            wallet=_WALLET,
            resource=_RESOURCE,
            store=promo_store,
        )
        for _ in range(5)
    ]

    assert [r is not None for r in results] == [True, True, True, True, False]
    # 4 real redemptions spent from the global total; the 5th (refused)
    # attempt's reservation was given back.
    assert fake_redis.store[f"{promo_module._REMAINING_PREFIX}LAUNCH"] == "21"
    # The per-wallet counter is fully spent (0), not negative -- the refused
    # 5th attempt's wallet-slot reservation was also given back.
    assert (
        fake_redis.store[
            f"{promo_module._WALLET_REMAINING_PREFIX}LAUNCH:{promo_module._hash_wallet(_WALLET)}"
        ]
        == "0"
    )


@pytest.mark.usefixtures("fake_redis")
def test_default_max_redemptions_per_wallet_is_still_exactly_one(
    promo_store: InMemoryPromoStore, caplog: pytest.LogCaptureFixture
) -> None:
    """A code seeded without max_redemptions_per_wallet keeps the pre-101 exactly-once behavior.

    Also: the second (refused) attempt logs an INFO line naming the reason (this wallet's own
    per-code allowance, not the code's global count) -- same operator-clarity follow-up as the
    exhausted-code and unknown-code tests above.
    """
    _seed(promo_store, starting_count=25)  # max_redemptions_per_wallet defaults to 1
    headers = {"x-real-ip": "1.1.1.1"}

    first = promo_module.attempt_promo_redemption(
        _request(headers=headers),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )
    with caplog.at_level("INFO", logger="app.modules.x402.promo"):
        second = promo_module.attempt_promo_redemption(
            _request(headers=headers),
            code="LAUNCH",
            wallet=_WALLET,
            resource=_RESOURCE,
            store=promo_store,
        )

    assert first is not None
    assert second is None
    assert any(
        "code=LAUNCH already redeemed max_redemptions_per_wallet=1 times" in record.message
        for record in caplog.records
    )


@pytest.mark.usefixtures("fake_redis")
def test_different_wallets_each_get_their_own_full_per_wallet_allowance(
    promo_store: InMemoryPromoStore,
) -> None:
    """A second, distinct wallet is unaffected by the first wallet exhausting its own cap."""
    _seed(promo_store, starting_count=25, max_redemptions_per_wallet=1)
    headers = {"x-real-ip": "1.1.1.1"}

    first_wallet_first = promo_module.attempt_promo_redemption(
        _request(headers=headers),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )
    first_wallet_second = promo_module.attempt_promo_redemption(
        _request(headers=headers),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )
    second_wallet_first = promo_module.attempt_promo_redemption(
        _request(headers=headers),
        code="LAUNCH",
        wallet=_WALLET_2,
        resource=_RESOURCE,
        store=promo_store,
    )

    assert first_wallet_first is not None
    assert first_wallet_second is None  # same wallet, already used its one slot
    assert second_wallet_first is not None  # different wallet, its own fresh allowance


def test_redemption_log_write_failure_undoes_both_global_and_wallet_slots(
    promo_store: InMemoryPromoStore, fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the audit-log write itself raises, both reserved slots are given back, not just the global one."""
    _seed(promo_store, starting_count=25, max_redemptions_per_wallet=4)

    def _boom(_self: InMemoryPromoStore, _item: RedemptionRecord) -> bool:
        raise RuntimeError("cassandra write failed")

    monkeypatch.setattr(InMemoryPromoStore, "insert_redemption_if_absent", _boom)

    result = promo_module.attempt_promo_redemption(
        _request(headers={"x-real-ip": "1.1.1.1"}),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )

    assert result is None
    assert fake_redis.store[f"{promo_module._REMAINING_PREFIX}LAUNCH"] == "25"
    assert (
        fake_redis.store[
            f"{promo_module._WALLET_REMAINING_PREFIX}LAUNCH:{promo_module._hash_wallet(_WALLET)}"
        ]
        == "4"
    )


def test_redis_down_fails_closed(
    promo_store: InMemoryPromoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis unreachable during redemption fails CLOSED: the attempt is refused and falls through to normal payment."""
    _seed(promo_store)
    monkeypatch.setattr(promo_module, "get_redis", lambda **_kw: _BrokenRedis())
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: _FakeRedis())

    result = promo_module.attempt_promo_redemption(
        _request(headers={"x-real-ip": "1.1.1.1"}),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )

    assert result is None


def test_invalid_wallet_falls_through(
    promo_store: InMemoryPromoStore, fake_redis: _FakeRedis
) -> None:
    """A syntactically invalid wallet address falls through without touching Redis."""
    _seed(promo_store)

    result = promo_module.attempt_promo_redemption(
        _request(headers={"x-real-ip": "1.1.1.1"}),
        code="LAUNCH",
        wallet="not-a-real-algorand-address",
        resource=_RESOURCE,
        store=promo_store,
    )

    assert result is None
    assert f"{promo_module._REMAINING_PREFIX}LAUNCH" not in fake_redis.store


@pytest.mark.usefixtures("fake_redis")
def test_unknown_code_falls_through(
    promo_store: InMemoryPromoStore, caplog: pytest.LogCaptureFixture
) -> None:
    """A code that was never issued falls through, now with an INFO log line for operators."""
    with caplog.at_level("INFO", logger="app.modules.x402.promo"):
        result = promo_module.attempt_promo_redemption(
            _request(headers={"x-real-ip": "1.1.1.1"}),
            code="NOPE",
            wallet=_WALLET,
            resource=_RESOURCE,
            store=promo_store,
        )
    assert result is None
    assert any(
        "no active code=NOPE valid for resource=" in record.message for record in caplog.records
    )


@pytest.mark.usefixtures("fake_redis")
def test_expired_code_falls_through(promo_store: InMemoryPromoStore) -> None:
    """A code past its expires_at_epoch falls through."""
    long_ago = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp())
    _seed(promo_store, expires_at_epoch=long_ago)

    result = promo_module.attempt_promo_redemption(
        _request(headers={"x-real-ip": "1.1.1.1"}),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )
    assert result is None


@pytest.mark.usefixtures("fake_redis")
def test_inactive_code_falls_through(promo_store: InMemoryPromoStore) -> None:
    """An admin-deactivated code falls through."""
    _seed(promo_store, active=False)

    result = promo_module.attempt_promo_redemption(
        _request(headers={"x-real-ip": "1.1.1.1"}),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )
    assert result is None


@pytest.mark.usefixtures("fake_redis")
def test_redemption_attempts_are_rate_limited_per_ip_and_fall_through(
    promo_store: InMemoryPromoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second attempt from the same IP within the attempt budget falls through (not an error); a different IP has its own budget."""
    _seed(promo_store, starting_count=100)
    monkeypatch.setattr(settings, "x402_promo_rate_limit_per_hour", 1)
    headers = {"x-real-ip": "9.9.9.9"}

    first = promo_module.attempt_promo_redemption(
        _request(headers=headers),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )
    second = promo_module.attempt_promo_redemption(
        _request(headers=headers),
        code="LAUNCH",
        wallet=_WALLET_2,
        resource=_RESOURCE,
        store=promo_store,
    )

    assert first is not None
    assert second is None  # rate-limited, falls through silently -- never an error
    # A different IP has its own budget.
    third = promo_module.attempt_promo_redemption(
        _request(headers={"x-real-ip": "9.9.9.10"}),
        code="LAUNCH",
        wallet=_WALLET_2,
        resource=_RESOURCE,
        store=promo_store,
    )
    assert third is not None


# --------------------------------------------------------------------------- #
# mark_fulfilled on a promo result is a safe no-op
# --------------------------------------------------------------------------- #
def test_mark_fulfilled_on_a_promo_result_is_a_safe_noop() -> None:
    """A route that accidentally calls mark_fulfilled on a promo result gets a clean no-op: no exception, no phantom row."""
    from app.modules.x402.paid_request import mark_fulfilled
    from app.modules.x402.settlement import InMemorySettlementStore

    store = InMemorySettlementStore()
    result = x402_guard.PaymentResult(error=None, is_promo=True)

    marked = mark_fulfilled(result.payment_txid, resource=_RESOURCE, store=store)

    assert marked is False
    assert store.settlements == []


# --------------------------------------------------------------------------- #
# Admin create / deactivate service functions
# --------------------------------------------------------------------------- #
def test_create_promo_code_rejects_a_duplicate(promo_store: InMemoryPromoStore) -> None:
    """A second create_promo_code for the same code is a 409 PromoError, not a silent overwrite."""
    promo_module.create_promo_code(
        code="LAUNCH", resource=_RESOURCE, starting_count=5, store=promo_store
    )
    with pytest.raises(PromoError) as excinfo:
        promo_module.create_promo_code(
            code="LAUNCH", resource=_RESOURCE, starting_count=5, store=promo_store
        )
    assert excinfo.value.http_status == 409


def test_create_promo_code_rejects_a_non_positive_count(promo_store: InMemoryPromoStore) -> None:
    """starting_count must be a positive integer."""
    with pytest.raises(PromoError):
        promo_module.create_promo_code(
            code="LAUNCH", resource=_RESOURCE, starting_count=0, store=promo_store
        )


def test_deactivate_promo_code_returns_false_for_an_unknown_code(
    promo_store: InMemoryPromoStore,
) -> None:
    """Deactivating a code that was never created returns False, not an error."""
    assert promo_module.deactivate_promo_code("NOPE", store=promo_store) is False


@pytest.mark.usefixtures("fake_redis")
def test_deactivate_promo_code_stops_further_redemptions(promo_store: InMemoryPromoStore) -> None:
    """A deactivated code is refused by attempt_promo_redemption too, not just by the admin routes."""
    _seed(promo_store)
    assert promo_module.deactivate_promo_code("LAUNCH", store=promo_store) is True

    result = promo_module.attempt_promo_redemption(
        _request(headers={"x-real-ip": "1.1.1.1"}),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )
    assert result is None


# --------------------------------------------------------------------------- #
# Admin HTTP routes
# --------------------------------------------------------------------------- #
def _admin_create_request(body: bytes) -> Request:
    return _request(method="POST", body=body, path="/api/v1/admin/x402/promo")


def _admin_delete_request(code: str) -> Request:
    return _request(method="DELETE", query={"code": code}, path="/api/v1/admin/x402/promo")


def _admin_list_request() -> Request:
    return _request(method="GET", path="/api/v1/admin/x402/promo")


def test_admin_list_promo_without_admin_wallet_is_rejected() -> None:
    """No admin session configured at all -> the real require_admin_wallet refuses, never a listing."""
    response = catalog_routes.x402_admin_list_promo(_admin_list_request())
    assert getattr(response, "status_code", 200) != 200


@pytest.mark.usefixtures("fake_redis")
def test_admin_list_promo_returns_every_code_with_remaining_count(
    promo_store: InMemoryPromoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The list route returns every stored code, each carrying its live remaining count."""
    monkeypatch.setattr(catalog_routes, "require_admin_wallet", lambda _r: None)
    _seed(promo_store, code="LAUNCH", starting_count=5)
    _seed(promo_store, code="OTHER", resource="x402-grading-score", starting_count=10)
    promo_module.set_promo_store(promo_store)
    try:
        response = catalog_routes.x402_admin_list_promo(_admin_list_request())
        assert isinstance(response, dict)
        items = {item["code"]: item for item in response["items"]}
        assert set(items) == {"LAUNCH", "OTHER"}
        # Neither code has been redeemed yet -- Redis key never seeded, so
        # remaining falls back to the durable starting_count, not 0 or None.
        assert items["LAUNCH"]["remaining"] == 5
        assert items["OTHER"]["remaining"] == 10
        assert items["LAUNCH"]["resource"] == _RESOURCE
        assert items["LAUNCH"]["active"] is True
    finally:
        promo_module.set_promo_store(None)


@pytest.mark.usefixtures("fake_redis")
def test_list_promo_codes_reflects_a_live_redemption(promo_store: InMemoryPromoStore) -> None:
    """After one redemption, list_promo_codes' remaining count drops by one."""
    _seed(promo_store, code="LAUNCH", starting_count=5)
    promo_module.attempt_promo_redemption(
        _request(headers={"x-real-ip": "1.1.1.1"}),
        code="LAUNCH",
        wallet=_WALLET,
        resource=_RESOURCE,
        store=promo_store,
    )

    items = promo_module.list_promo_codes(store=promo_store)

    assert items[0]["remaining"] == 4


def test_list_promo_codes_remaining_is_none_when_redis_unreachable(
    promo_store: InMemoryPromoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis being down degrades just the remaining field to None -- the listing itself still succeeds."""
    _seed(promo_store, code="LAUNCH", starting_count=5)
    monkeypatch.setattr(promo_module, "get_redis", lambda **_kw: _BrokenRedis())

    items = promo_module.list_promo_codes(store=promo_store)

    assert items[0]["code"] == "LAUNCH"
    assert items[0]["remaining"] is None
    assert items[0]["starting_count"] == 5


def test_admin_create_promo_without_admin_wallet_is_rejected() -> None:
    """No admin session configured at all -> the real require_admin_wallet 503s (ADMIN_WALLET_ADDRESSES unset in tests)."""
    response = catalog_routes.x402_admin_create_promo(
        _admin_create_request(b'{"code":"LAUNCH","resource":"x402-scan-url","starting_count":5}')
    )
    assert getattr(response, "status_code", 200) != 200


def test_admin_create_promo_wrong_wallet_is_rejected(
    promo_store: InMemoryPromoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session that resolves to a non-admin wallet is refused, and nothing is created."""
    monkeypatch.setattr(
        catalog_routes,
        "require_admin_wallet",
        lambda _r: json_error_response(403, "forbidden", "not an admin wallet"),
    )
    promo_module.set_promo_store(promo_store)
    try:
        response = catalog_routes.x402_admin_create_promo(
            _admin_create_request(
                b'{"code":"LAUNCH","resource":"x402-scan-url","starting_count":5}'
            )
        )
        assert response.status_code == 403
        assert promo_store.get_code("LAUNCH") is None
    finally:
        promo_module.set_promo_store(None)


def test_admin_create_promo_succeeds_and_is_readable_back(
    promo_store: InMemoryPromoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An authorized admin create stores the code and returns its fields."""
    monkeypatch.setattr(catalog_routes, "require_admin_wallet", lambda _r: None)
    promo_module.set_promo_store(promo_store)
    try:
        response = catalog_routes.x402_admin_create_promo(
            _admin_create_request(
                b'{"code":"LAUNCH","resource":"x402-scan-url","starting_count":5,"expires_at_epoch":0}'
            )
        )
        assert isinstance(response, dict)
        assert response["code"] == "LAUNCH"
        assert response["resource"] == "x402-scan-url"
        assert response["starting_count"] == 5
        assert response["expires_at_epoch"] == 0
        assert response["active"] is True
        assert response["created_at_epoch"] > 0
        assert promo_store.get_code("LAUNCH") is not None
    finally:
        promo_module.set_promo_store(None)


def test_admin_create_promo_duplicate_code_is_409(
    promo_store: InMemoryPromoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating a code that already exists is a 409, surfaced through the HTTP route."""
    monkeypatch.setattr(catalog_routes, "require_admin_wallet", lambda _r: None)
    _seed(promo_store)
    promo_module.set_promo_store(promo_store)
    try:
        response = catalog_routes.x402_admin_create_promo(
            _admin_create_request(
                b'{"code":"LAUNCH","resource":"x402-scan-url","starting_count":5}'
            )
        )
        assert response.status_code == 409
    finally:
        promo_module.set_promo_store(None)


def test_admin_delete_promo_without_admin_wallet_is_rejected() -> None:
    """No admin session at all -> whatever require_admin_wallet returns, never a check result."""
    response = catalog_routes.x402_admin_delete_promo(_admin_delete_request("LAUNCH"))
    assert getattr(response, "status_code", 200) != 200


def test_admin_delete_promo_wrong_wallet_is_rejected(
    promo_store: InMemoryPromoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session that resolves to a non-admin wallet is refused, and nothing is deactivated."""
    _seed(promo_store)
    monkeypatch.setattr(
        catalog_routes,
        "require_admin_wallet",
        lambda _r: json_error_response(403, "forbidden", "not an admin wallet"),
    )
    promo_module.set_promo_store(promo_store)
    try:
        response = catalog_routes.x402_admin_delete_promo(_admin_delete_request("LAUNCH"))
        assert response.status_code == 403
        assert promo_store.get_code("LAUNCH") is not None
        assert promo_store.get_code("LAUNCH").active is True
    finally:
        promo_module.set_promo_store(None)


def test_admin_delete_promo_deactivates(
    promo_store: InMemoryPromoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An authorized admin delete flips the code inactive."""
    _seed(promo_store)
    monkeypatch.setattr(catalog_routes, "require_admin_wallet", lambda _r: None)
    promo_module.set_promo_store(promo_store)
    try:
        response = catalog_routes.x402_admin_delete_promo(_admin_delete_request("LAUNCH"))
        assert response == {"deactivated": True, "code": "LAUNCH"}
        assert promo_store.get_code("LAUNCH").active is False
    finally:
        promo_module.set_promo_store(None)


def test_admin_delete_promo_nonexistent_code_returns_404(
    promo_store: InMemoryPromoStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a code that was never created is a 404, not a silent no-op success."""
    monkeypatch.setattr(catalog_routes, "require_admin_wallet", lambda _r: None)
    promo_module.set_promo_store(promo_store)
    try:
        response = catalog_routes.x402_admin_delete_promo(_admin_delete_request("NEVER-ISSUED"))
        assert response.status_code == 404
    finally:
        promo_module.set_promo_store(None)


# --------------------------------------------------------------------------- #
# x402_admin_reset_refund_breaker
# --------------------------------------------------------------------------- #
def _admin_reset_breaker_request(resource: str) -> Request:
    return _request(
        method="POST",
        query={"resource": resource},
        path="/api/v1/admin/x402/refund-breaker/reset",
    )


def test_admin_reset_refund_breaker_without_admin_wallet_is_rejected() -> None:
    """No admin session configured at all -> the real require_admin_wallet refuses."""
    response = catalog_routes.x402_admin_reset_refund_breaker(
        _admin_reset_breaker_request("x402-scan-url")
    )
    assert getattr(response, "status_code", 200) != 200


def test_admin_reset_refund_breaker_requires_a_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing ?resource= is a free 400, never a silent no-op."""
    monkeypatch.setattr(catalog_routes, "require_admin_wallet", lambda _r: None)

    response = catalog_routes.x402_admin_reset_refund_breaker(_admin_reset_breaker_request(""))

    assert response.status_code == 400


def test_admin_reset_refund_breaker_clears_a_tripped_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An authorized reset actually un-trips the breaker for that resource."""
    monkeypatch.setattr(catalog_routes, "require_admin_wallet", lambda _r: None)
    monkeypatch.setattr(settings, "x402_refund_breaker_max_failures", 1)
    fake = _FakeRedis()
    monkeypatch.setattr(circuit_breaker, "get_redis", lambda **_kw: fake)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: fake)
    circuit_breaker.record_refund_failure("x402-scan-url")
    assert circuit_breaker.is_tripped("x402-scan-url") is True

    response = catalog_routes.x402_admin_reset_refund_breaker(
        _admin_reset_breaker_request("x402-scan-url")
    )

    assert response == {"reset": True, "resource": "x402-scan-url"}
    assert circuit_breaker.is_tripped("x402-scan-url") is False


# --------------------------------------------------------------------------- #
# Cassandra statement sequence (migration 100)
# --------------------------------------------------------------------------- #
def test_redemption_insert_is_a_single_plain_insert_no_phantom_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """insert_redemption_if_absent is one plain INSERT (migration 101: redemption_id is a server-generated now() timeuuid, so IF NOT EXISTS would never reject anything) -- never a separate read-then-write, and always returns True on a successful write."""
    session = _patch_cassandra(monkeypatch)
    session.execute.return_value = SimpleNamespace()

    applied = CassandraPromoStore().insert_redemption_if_absent(
        RedemptionRecord(
            code="LAUNCH", wallet_hash="deadbeef", redeemed_at_epoch=1000, resource=_RESOURCE
        )
    )

    assert applied is True
    pairs = execute_pairs(session)
    assert len(pairs) == 1
    stmt, params = pairs[0]
    assert "x402_promo_redemptions_v2" in stmt
    assert "IF NOT EXISTS" not in stmt
    assert "now()" in stmt
    assert params[0] == "LAUNCH"
    assert params[1] == "deadbeef"
    assert params[3] == _RESOURCE


def test_redemption_insert_failure_raises_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real write failure (e.g. Cassandra unreachable) raises -- attempt_promo_redemption is the layer that catches it and undoes both reserved slots, not this store method."""
    session = _patch_cassandra(monkeypatch)
    session.execute.side_effect = RuntimeError("cassandra write failed")

    with pytest.raises(RuntimeError):
        CassandraPromoStore().insert_redemption_if_absent(
            RedemptionRecord(
                code="LAUNCH", wallet_hash="deadbeef", redeemed_at_epoch=1000, resource=_RESOURCE
            )
        )


def test_create_code_uses_a_lightweight_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_code is a single INSERT ... IF NOT EXISTS, never a separate exists-check."""
    session = _patch_cassandra(monkeypatch)
    session.execute.return_value = SimpleNamespace(was_applied=True)

    applied = CassandraPromoStore().create_code(
        PromoRecord(
            code="LAUNCH",
            resource=_RESOURCE,
            starting_count=5,
            created_at_epoch=1000,
            expires_at_epoch=0,
            active=True,
        )
    )

    assert applied is True
    pairs = execute_pairs(session)
    assert len(pairs) == 1
    stmt, params = pairs[0]
    assert "x402_promo_codes" in stmt
    assert "IF NOT EXISTS" in stmt
    assert params[0] == "LAUNCH"
    assert params[4] is None  # expires_at: no expiry


def test_deactivate_code_uses_update_if_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """deactivate_code is UPDATE ... IF EXISTS, so it can never upsert a phantom row for a code that was never created."""
    session = _patch_cassandra(monkeypatch)
    session.execute.return_value = SimpleNamespace(was_applied=True)

    applied = CassandraPromoStore().deactivate_code("LAUNCH")

    assert applied is True
    pairs = execute_pairs(session)
    stmt, params = pairs[0]
    assert "IF EXISTS" in stmt
    assert params == ("LAUNCH",)


def test_list_codes_reads_back_every_row_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_codes maps every row from the bounded LIST_ALL_PROMO_CODES scan back onto PromoRecord."""
    rows = [
        SimpleNamespace(
            code="LAUNCH",
            resource=_RESOURCE,
            starting_count=5,
            created_at=datetime(2026, 8, 30, tzinfo=UTC),
            expires_at=None,
            active=True,
            max_redemptions_per_wallet=1,
        ),
        SimpleNamespace(
            code="OTHER",
            resource="x402-grading-score",
            starting_count=10,
            created_at=datetime(2026, 8, 31, tzinfo=UTC),
            expires_at=None,
            active=False,
            max_redemptions_per_wallet=4,
        ),
    ]
    session = _patch_cassandra(monkeypatch)
    session.execute.return_value = rows

    records = CassandraPromoStore().list_codes()

    assert [r.code for r in records] == ["LAUNCH", "OTHER"]
    assert records[1].active is False
    # No bind params (a full-partition scan, same convention as
    # GlossaryStmts.LIST_ALL), so this isn't an execute_pairs case.
    stmt = session.execute.call_args.args[0]
    assert "x402_promo_codes" in stmt
    assert "LIMIT 500" in stmt
    assert "WHERE" not in stmt


def test_get_code_reads_back_a_full_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_code maps every column back onto PromoRecord, including a null expires_at as no-expiry (0)."""
    row = SimpleNamespace(
        code="LAUNCH",
        resource=_RESOURCE,
        starting_count=5,
        created_at=datetime(2026, 8, 30, tzinfo=UTC),
        expires_at=None,
        active=True,
        max_redemptions_per_wallet=None,
    )
    session = _patch_cassandra(monkeypatch)
    session.execute.return_value = SimpleNamespace(one=lambda: row)

    record = CassandraPromoStore().get_code("LAUNCH")

    assert record == PromoRecord(
        code="LAUNCH",
        resource=_RESOURCE,
        starting_count=5,
        created_at_epoch=int(row.created_at.timestamp()),
        expires_at_epoch=0,
        active=True,
        max_redemptions_per_wallet=1,
    )
