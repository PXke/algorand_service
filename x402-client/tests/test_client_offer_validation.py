"""PxkeClient's pre-sign 402 offer validation (recipient allowlist + amount cap).

Regression coverage for the 2026-09-07 security review, finding 3: before
this, the client handed a 402 response straight to the payment library with
no check at all -- a malicious or compromised response (a MITM, a DNS
hijack, or a compromise of PXke's own server) could redirect a real mainnet
payment to an attacker's address, or inflate the amount, and a headless
agent holding a funded mnemonic would sign and broadcast it with no human
in the loop to notice. `_validate_offer` now runs BEFORE the payment
client ever builds or signs anything.
"""

from __future__ import annotations

import pytest

from pxke_x402 import PxkeClient
from pxke_x402.exceptions import PxkeOfferValidationError

from .conftest import FakePaymentHTTPClient, FakeResponse, FakeSession
from .conftest import valid_offer_headers as _offer_headers

_REAL_PAY_TO = "KSAVOYTVNB7A6NKCM4W2WBOOGFHWH2SEGR5T6OGB7THCAT5E36LDFEBTII"
_ATTACKER_PAY_TO = "A" * 58


def test_a_valid_in_policy_offer_is_paid_normally() -> None:
    """The happy path: default validation is on, and a real, in-policy offer is unaffected."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"pong": True, "settlement_tx_id": "TX1"}),
        ]
    )
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    result = client.ping()

    assert result == {"pong": True, "settlement_tx_id": "TX1"}
    assert len(fake_payment.calls) == 1


def test_an_offer_paying_a_different_address_is_refused_before_signing() -> None:
    """The core attack this closes: a 402 offer naming an unexpected recipient must never reach the signer."""
    session = FakeSession([FakeResponse(402, headers=_offer_headers(pay_to=_ATTACKER_PAY_TO))])
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    with pytest.raises(PxkeOfferValidationError) as excinfo:
        client.ping()

    assert _ATTACKER_PAY_TO in str(excinfo.value)
    assert fake_payment.calls == []  # never even reached the payment client
    assert len(session.calls) == 1  # no retry, nothing signed or sent


def test_an_offer_over_the_amount_cap_is_refused_before_signing() -> None:
    """A wildly inflated amount (e.g. a compromised server) is refused, not silently signed."""
    session = FakeSession([FakeResponse(402, headers=_offer_headers(amount="50000000"))])
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    with pytest.raises(PxkeOfferValidationError) as excinfo:
        client.ping()

    assert "50000000" in str(excinfo.value)
    assert fake_payment.calls == []
    assert len(session.calls) == 1


def test_an_offer_at_exactly_the_cap_is_accepted() -> None:
    """The boundary: an amount equal to (not over) the cap passes."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers(amount="1000000")),
            FakeResponse(200, {"pong": True, "settlement_tx_id": "TX1"}),
        ]
    )
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    client.ping()

    assert len(fake_payment.calls) == 1


def test_expected_pay_to_none_disables_the_recipient_check() -> None:
    """An explicit opt-out is honored -- a caller who really wants this off can turn it off."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers(pay_to=_ATTACKER_PAY_TO)),
            FakeResponse(200, {"pong": True, "settlement_tx_id": "TX1"}),
        ]
    )
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment, expected_pay_to=None)

    client.ping()

    assert len(fake_payment.calls) == 1


def test_max_payment_atomic_none_disables_the_amount_check() -> None:
    """An explicit opt-out for the amount cap is honored the same way."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers(amount="999999999")),
            FakeResponse(200, {"pong": True, "settlement_tx_id": "TX1"}),
        ]
    )
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment, max_payment_atomic=None)

    client.ping()

    assert len(fake_payment.calls) == 1


def test_a_custom_expected_pay_to_set_is_honored() -> None:
    """A caller can widen (or replace) the recipient allowlist, e.g. a second legitimate address."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers(pay_to=_ATTACKER_PAY_TO)),
            FakeResponse(200, {"pong": True, "settlement_tx_id": "TX1"}),
        ]
    )
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(
        session=session,
        http_client=fake_payment,
        expected_pay_to=[_REAL_PAY_TO, _ATTACKER_PAY_TO],
    )

    client.ping()

    assert len(fake_payment.calls) == 1
