"""Multi-asset x402 offer tests for the shared payment gate.

Everything here is offline: a stub facilitator (no network), a stubbed price
oracle (no CoinGecko), and no real wallet. The MainNet CAIP-2 id appears only
as a test constant — `settings.x402_network` itself is monkeypatched per test
and never flipped in config.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Never

import pytest

pytest.importorskip("x402")

from x402.http.constants import PAYMENT_SIGNATURE_HEADER
from x402.http.utils import (
    decode_payment_required_header,
    encode_payment_signature_header,
)
from x402.mechanisms.avm.constants import (
    ALGORAND_MAINNET_CAIP2,
    ALGORAND_TESTNET_CAIP2,
    USDC_MAINNET_ASA_ID,
    USDC_TESTNET_ASA_ID,
)
from x402.schemas.payments import PaymentPayload, PaymentRequirements
from x402.schemas.responses import SettleResponse, SupportedKind, SupportedResponse, VerifyResponse
from x402.schemas.v1 import PaymentRequirementsV1
from x402.server import x402ResourceServerSync

from app.core.config import settings
from app.core.http import QueryParams, Request
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard
from app.modules.x402.assets import EURQ, USDQ

_PAY_TO = "A" * 58
_PAYER = "P" * 58

# Rates as returned live by CoinGecko when this was built. Fixed here so the
# expected atomic amounts below are hand-checkable arithmetic, not a snapshot
# of whatever the market did today.
_RATES = {EURQ.coingecko_id: Decimal("1.12"), USDQ.coingecko_id: Decimal("0.998693")}


def _fake_request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        method="GET",
        headers=headers or {},
        query_params=QueryParams(),
        path_params={},
        body=b"",
        url=SimpleNamespace(scheme="http", host="localhost", path="/api/v1/x402/thing"),
    )


class _StubFacilitator:
    """Canned /supported for one network. verify()/settle() raise unless a test opts in."""

    def __init__(self, network: str) -> None:
        self._network = network

    def get_supported(self) -> SupportedResponse:
        return SupportedResponse(
            kinds=[SupportedKind(x402_version=2, scheme="exact", network=self._network)]
        )

    def verify(
        self, _payload: object, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("verify() must not be called without a payment header")

    def settle(
        self, _payload: object, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("settle() must not be called without a payment header")


class _SettlingFacilitator(_StubFacilitator):
    """Accepts and settles whatever it is handed, recording the requirements it saw."""

    def __init__(self, network: str) -> None:
        super().__init__(network)
        self.settled: list[PaymentRequirements] = []

    def verify(
        self, _payload: object, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> VerifyResponse:
        return VerifyResponse(is_valid=True, payer=_PAYER)

    def settle(
        self, _payload: object, requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> SettleResponse:
        self.settled.append(requirements)
        return SettleResponse(
            success=True,
            transaction="TX123",
            network=str(requirements.network),
            payer=_PAYER,
        )


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    network: str,
    facilitator: _StubFacilitator | None = None,
    rates: dict[str, Decimal] | None = None,
) -> None:
    """Point the gate at `network`, an offline facilitator, and a stubbed oracle."""
    client = facilitator or _StubFacilitator(network)

    def _server() -> x402ResourceServerSync:
        server = x402ResourceServerSync(client)
        x402_client.register_tagged_exact_avm_scheme(server, network)
        server.initialize()
        return server

    resolved = _RATES if rates is None else rates
    monkeypatch.setattr(x402_guard, "get_resource_server", _server)
    monkeypatch.setattr(x402_client, "get_usd_rate", lambda asset: resolved.get(asset))
    monkeypatch.setattr(settings, "x402_network", network)
    monkeypatch.setattr(settings, "x402_pay_to_address", _PAY_TO)


def _offer(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> list[PaymentRequirements]:
    """The `accepts` list from the 402 a payment-less request receives."""
    _install(monkeypatch, **kwargs)  # type: ignore[arg-type]
    result = x402_guard.require_payment(
        _fake_request(), price="$0.10", resource="thing", description="A thing."
    )
    assert result.error is not None
    return decode_payment_required_header(result.error.headers["PAYMENT-REQUIRED"]).accepts


def test_mainnet_offer_lists_every_accepted_asset_usdc_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 402 offers USDC, EURQ and USDQ, in that order."""
    # Order is the functional expression of "USDC preferred": the package's
    # default client selector is `return requirements[0]`, so a well-behaved
    # agent pays in whichever asset is listed first.
    accepts = _offer(monkeypatch, network=ALGORAND_MAINNET_CAIP2)

    assert [option.asset for option in accepts] == [
        str(USDC_MAINNET_ASA_ID),
        str(EURQ.asa_ids[ALGORAND_MAINNET_CAIP2]),
        str(USDQ.asa_ids[ALGORAND_MAINNET_CAIP2]),
    ]


def test_every_accepted_asset_carries_the_challenge_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every asset's requirements carry the contest tag, not just USDC's."""
    # The whole point of the tag is that ANY settled payment through us
    # qualifies for the leaderboard, whichever asset the payer chose.
    accepts = _offer(monkeypatch, network=ALGORAND_MAINNET_CAIP2)

    assert len(accepts) == 3
    for option in accepts:
        assert option.extra["tag"] == x402_client.CHALLENGE_TAG
        assert option.extra["decimals"] == 6


def test_each_asset_is_priced_from_the_same_dollar_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A $0.10 price becomes the right quantity of each asset at its own USD rate."""
    # Hand-checkable: units = usd / usd_per_unit, then scaled to 6 decimals.
    #   USDC  0.10 / 1.000000 = 0.100000    -> 100000
    #   EURQ  0.10 / 1.120000 = 0.08928571… ->  89286 (rounded up)
    #   USDQ  0.10 / 0.998693 = 0.10013087… -> 100131 (rounded up)
    # Note the direction: EURQ is worth MORE than a dollar, so $0.10 buys
    # FEWER EURQ. Multiplying instead of dividing would be a 25% overcharge
    # here and a 12x undercharge on a sub-dollar asset.
    accepts = _offer(monkeypatch, network=ALGORAND_MAINNET_CAIP2)

    assert [option.amount for option in accepts] == ["100000", "89286", "100131"]


def test_a_cheaper_asset_costs_more_units(monkeypatch: pytest.MonkeyPatch) -> None:
    """Halving an asset's USD rate doubles the units charged."""
    # A standalone guard on the conversion's direction, independent of the
    # exact rates above: this fails loudly if the division is ever inverted.
    dear = _offer(
        monkeypatch,
        network=ALGORAND_MAINNET_CAIP2,
        rates={EURQ.coingecko_id: Decimal("2"), USDQ.coingecko_id: Decimal("1")},
    )
    cheap = _offer(
        monkeypatch,
        network=ALGORAND_MAINNET_CAIP2,
        rates={EURQ.coingecko_id: Decimal("1"), USDQ.coingecko_id: Decimal("1")},
    )

    assert int(cheap[1].amount) == 2 * int(dear[1].amount)


def test_an_asset_without_a_rate_is_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """An asset the oracle cannot price drops out of the offer; the rest still stand."""
    # Cold start with no last-known-good: quoting a broken price is worse than
    # quoting one asset fewer. USDC needs no oracle, so it is never affected.
    accepts = _offer(
        monkeypatch,
        network=ALGORAND_MAINNET_CAIP2,
        rates={USDQ.coingecko_id: Decimal("0.998693")},
    )

    assert [option.asset for option in accepts] == [
        str(USDC_MAINNET_ASA_ID),
        str(USDQ.asa_ids[ALGORAND_MAINNET_CAIP2]),
    ]


def test_offer_falls_back_to_usdc_only_when_no_rates_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A total oracle outage still leaves a payable USDC offer."""
    accepts = _offer(monkeypatch, network=ALGORAND_MAINNET_CAIP2, rates={})

    assert [option.asset for option in accepts] == [str(USDC_MAINNET_ASA_ID)]
    assert accepts[0].extra["tag"] == x402_client.CHALLENGE_TAG


def test_testnet_offers_usdc_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """On TestNet, where EURQ and USDQ do not exist, the offer is unchanged from before multi-asset."""
    accepts = _offer(monkeypatch, network=ALGORAND_TESTNET_CAIP2)

    assert [option.asset for option in accepts] == [str(USDC_TESTNET_ASA_ID)]
    assert accepts[0].amount == "100000"


def test_description_carries_the_preference_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """A multi-asset offer tells the payer in words which asset is preferred."""
    _install(monkeypatch, network=ALGORAND_MAINNET_CAIP2)

    result = x402_guard.require_payment(
        _fake_request(), price="$0.10", resource="thing", description="A thing."
    )

    decoded = decode_payment_required_header(result.error.headers["PAYMENT-REQUIRED"])
    assert decoded.resource.description == (
        "A thing. (USDC preferred; EURQ and USDQ also accepted.)"
    )


def test_single_asset_offer_has_no_preference_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a network with only one accepted asset the description is left exactly as the route wrote it."""
    # Claiming EURQ and USDQ are accepted on TestNet, where they do not exist,
    # would simply be false.
    _install(monkeypatch, network=ALGORAND_TESTNET_CAIP2)

    result = x402_guard.require_payment(
        _fake_request(), price="$0.10", resource="thing", description="A thing."
    )

    decoded = decode_payment_required_header(result.error.headers["PAYMENT-REQUIRED"])
    assert decoded.resource.description == "A thing."


@pytest.mark.parametrize("chosen_index", [0, 1, 2])
def test_payment_result_reports_the_asset_actually_settled(
    monkeypatch: pytest.MonkeyPatch, chosen_index: int
) -> None:
    """Paying in any offered asset is reflected in PaymentResult.asset_id/.network."""
    # This is what lets every product module stay unmodified: they read the
    # matched requirements, never a hardcoded USDC.
    facilitator = _SettlingFacilitator(ALGORAND_MAINNET_CAIP2)
    accepts = _offer(monkeypatch, network=ALGORAND_MAINNET_CAIP2, facilitator=facilitator)
    chosen = accepts[chosen_index]

    header = encode_payment_signature_header(
        PaymentPayload(x402_version=2, payload={}, accepted=chosen)
    )
    result = x402_guard.require_payment(
        _fake_request({PAYMENT_SIGNATURE_HEADER: header}),
        price="$0.10",
        resource="thing",
        description="A thing.",
    )

    assert result.error is None
    assert result.asset_id == chosen.asset
    assert result.amount_atomic == chosen.amount
    assert result.network == ALGORAND_MAINNET_CAIP2
    assert result.payer == _PAYER
    # And the facilitator settled that same asset, not the first one offered.
    assert facilitator.settled[-1].asset == chosen.asset
