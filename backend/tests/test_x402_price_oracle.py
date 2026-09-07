"""x402 price-oracle tests.

Fully offline. The cache-ladder tests fake the batch fetch at
`_fetch_rates`; the wire-shape tests drive the real httpx code path through an
`httpx.MockTransport`, which is the same seam media/api/routes.py uses. Neither
opens a socket — conftest.py blocks that process-wide anyway.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Never

import httpx
import pytest

pytest.importorskip("x402")

from app.modules.x402 import price_oracle

_EURQ = "quantoz-eurq"
_USDQ = "quantoz-usdq"
_GOBTC = "gobtc"
_USDC = price_oracle.USDC_COINGECKO_ID


class _FakeRedis:
    """Enough of the Redis API for the oracle: get/set/exists, with TTLs recorded.

    TTLs are recorded rather than enforced — a test expires a key by deleting
    it, which is what an elapsed TTL looks like to every read in this module.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.expires[key] = ex
        return True

    def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    def expire_now(self, key: str) -> None:
        """Simulate a TTL elapsing."""
        self.store.pop(key, None)
        self.expires.pop(key, None)


class _BrokenRedis:
    """Every operation fails, to exercise the fail-open path."""

    def get(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def set(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def exists(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Swap the oracle's Redis seam for an in-process fake."""
    client = _FakeRedis()
    monkeypatch.setattr(price_oracle, "get_redis", lambda **_kw: client)
    return client


def _fresh_key(coingecko_id: str, currency: str = "usd") -> str:
    return price_oracle._cache_key(price_oracle._FRESH_KEY, currency, coingecko_id)


def _lkg_key(coingecko_id: str, currency: str = "usd") -> str:
    return price_oracle._cache_key(price_oracle._LKG_KEY, currency, coingecko_id)


def _counting_fetch(
    monkeypatch: pytest.MonkeyPatch,
    rates: dict[str, Decimal],
    eur_rates: dict[str, Decimal] | None = None,
) -> list[int]:
    """Replace the batch fetch with canned USD (and optional EUR) rates, returning a call counter."""
    calls = [0]

    def _fetch(**_kwargs: object) -> price_oracle.FetchedRates:
        calls[0] += 1
        fetched: price_oracle.FetchedRates = {}
        if rates:
            fetched["usd"] = dict(rates)
        if eur_rates:
            fetched["eur"] = dict(eur_rates)
        return fetched

    monkeypatch.setattr(price_oracle, "_fetch_rates", _fetch)
    return calls


def test_fresh_fetch_populates_the_cache(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cold read fetches, returns the rate, and writes both the fresh and last-known-good keys."""
    _counting_fetch(monkeypatch, {_EURQ: Decimal("1.12"), _USDQ: Decimal("0.998693")})

    assert price_oracle.get_usd_rate(_EURQ) == Decimal("1.12")

    assert fake_redis.store[_fresh_key(_EURQ)] == "1.12"
    assert fake_redis.store[_lkg_key(_EURQ)] == "1.12"
    # The fresh key's TTL is the whole cache policy — an hour, per the owner's
    # "prices should refresh hourly".
    assert fake_redis.expires[_fresh_key(_EURQ)] == 3600
    # The last-known-good copy must NOT expire, or the stale fallback would
    # evaporate during exactly the outage it exists for.
    assert _lkg_key(_EURQ) not in fake_redis.expires


def test_cached_fresh_rate_is_served_without_refetching(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rate still inside its TTL is served straight from Redis, with no HTTP call."""
    fake_redis.store[_fresh_key(_EURQ)] = "1.10"
    calls = _counting_fetch(monkeypatch, {_EURQ: Decimal("9.99")})

    assert price_oracle.get_usd_rate(_EURQ) == Decimal("1.10")
    assert calls[0] == 0


def test_expired_cache_triggers_exactly_one_refetch(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired cache refetches once, and that one batch call refreshes every asset."""
    calls = _counting_fetch(monkeypatch, {_EURQ: Decimal("1.12"), _USDQ: Decimal("0.998693")})

    assert price_oracle.get_usd_rate(_EURQ) == Decimal("1.12")
    assert calls[0] == 1

    # The second asset was refreshed by the same batch call, so asking for it
    # must not spend another one — this is the batching requirement.
    assert price_oracle.get_usd_rate(_USDQ) == Decimal("0.998693")
    assert calls[0] == 1

    # Expiring one key sends us back for exactly one more batch, not one per asset.
    fake_redis.expire_now(_fresh_key(_EURQ))
    assert price_oracle.get_usd_rate(_EURQ) == Decimal("1.12")
    assert calls[0] == 2


def test_failed_refetch_falls_back_to_last_known_good(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the refetch fails, the last-known-good rate is served rather than breaking the payment flow."""
    fake_redis.store[_lkg_key(_EURQ)] = "1.09"
    _counting_fetch(monkeypatch, {})  # empty == fetch failed

    assert price_oracle.get_usd_rate(_EURQ) == Decimal("1.09")


@pytest.mark.usefixtures("fake_redis")
def test_cold_start_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no cached rate and a failing fetch, the answer is a clean None — never a zero or a raise."""
    _counting_fetch(monkeypatch, {})

    assert price_oracle.get_usd_rate(_EURQ) is None


def test_repeated_failures_are_rate_limited_by_a_cooldown(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed fetch arms a cooldown so a CoinGecko outage does not put an HTTP timeout in front of every paid request."""
    calls = _counting_fetch(monkeypatch, {})

    assert price_oracle.get_usd_rate(_EURQ) is None
    assert price_oracle.get_usd_rate(_EURQ) is None
    assert calls[0] == 1
    assert fake_redis.expires[price_oracle._COOLDOWN_KEY] == 60


def test_redis_outage_returns_none_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage degrades to "no rate" — the asset drops out of the offer, nothing raises into the payment path."""
    monkeypatch.setattr(price_oracle, "get_redis", lambda **_kw: _BrokenRedis())

    assert price_oracle.get_usd_rate(_EURQ) is None


def test_fetch_parses_the_real_coingecko_shape() -> None:
    """The live CoinGecko response shape is parsed into Decimal rates, and every id is requested in one call."""
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "algorand": {"usd": 0.08694, "eur": 0.0745},
                "quantoz-eurq": {"usd": 1.12, "eur": 1.0},
                "quantoz-usdq": {"usd": 0.998693, "eur": 0.8912},
                "gobtc": {"usd": 77990.0, "eur": 71822.0},
                "usd-coin": {"usd": 1.0, "eur": 0.8924},
            },
        )

    rates = _fetch_with(_handler)

    assert rates["usd"][_EURQ] == Decimal("1.12")
    assert rates["usd"][_USDQ] == Decimal("0.998693")
    assert rates["usd"][_GOBTC] == Decimal("77990.0")
    assert rates["eur"][_EURQ] == Decimal("1.0")
    assert rates["eur"][_USDQ] == Decimal("0.8912")
    assert rates["eur"][_GOBTC] == Decimal("71822.0")
    # USDC is fetched for its EUR quote only (the ledger needs it); it stays
    # oracle-free for the 402 offer.
    assert rates["eur"][_USDC] == Decimal("0.8924")
    # One request, carrying every priced id and both quote currencies.
    assert len(seen) == 1
    requested = seen[0].url.params["ids"].split(",")
    assert set(requested) == {_EURQ, _USDQ, _GOBTC, _USDC}
    assert set(seen[0].url.params["vs_currencies"].split(",")) == {"usd", "eur"}


def test_fetch_rejects_unusable_rates() -> None:
    """Missing, non-numeric, and non-positive rates are dropped rather than becoming a price."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "quantoz-eurq": {"usd": "not-a-number", "eur": -1},
                "quantoz-usdq": {"usd": 0, "eur": True},
                "usd-coin": {"usd": 1.0},
            },
        )

    # usd-coin's usd quote is the only usable value; every eur one was dropped.
    assert _fetch_with(_handler) == {"usd": {_USDC: Decimal("1.0")}}


def test_fetch_returns_empty_on_http_error() -> None:
    """A CoinGecko 5xx yields no rates rather than an exception escaping into the payment path."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    assert _fetch_with(_handler) == {}


def test_eur_rate_uses_its_own_cache_keys_and_the_same_batch(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A EUR read is served from the same one fetch as USD, under separate keys, so neither overwrites the other."""
    calls = _counting_fetch(
        monkeypatch,
        {_EURQ: Decimal("1.12")},
        eur_rates={_EURQ: Decimal("1.0"), _USDC: Decimal("0.89")},
    )

    assert price_oracle.get_eur_rate(_USDC) == Decimal("0.89")
    assert price_oracle.get_usd_rate(_EURQ) == Decimal("1.12")
    assert price_oracle.get_eur_rate(_EURQ) == Decimal("1.0")
    assert calls[0] == 1
    assert fake_redis.store[_fresh_key(_EURQ, "eur")] == "1.0"
    assert fake_redis.store[_fresh_key(_EURQ, "usd")] == "1.12"
    assert fake_redis.store[_lkg_key(_USDC, "eur")] == "0.89"
    assert _lkg_key(_USDC, "eur") not in fake_redis.expires


def test_eur_rate_falls_back_to_last_known_good_then_none(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The EUR ladder degrades exactly like the USD one: stale value, then a clean None, never a zero or a raise."""
    fake_redis.store[_lkg_key(_USDC, "eur")] = "0.88"
    _counting_fetch(monkeypatch, {})

    assert price_oracle.get_eur_rate(_USDC) == Decimal("0.88")
    assert price_oracle.get_eur_rate(_EURQ) is None


def test_eur_rate_redis_outage_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage yields no EUR valuation rather than an exception in the settlement path."""
    monkeypatch.setattr(price_oracle, "get_redis", lambda **_kw: _BrokenRedis())

    assert price_oracle.get_eur_rate(_USDC) is None


def _fetch_with(handler: object) -> price_oracle.FetchedRates:
    """Run the real fetch against a mocked transport."""
    return price_oracle._fetch_rates(transport=httpx.MockTransport(handler))
