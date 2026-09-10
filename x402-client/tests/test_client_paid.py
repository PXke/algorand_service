"""Paid methods: the 402 -> sign -> retry flow, faked at the payment-client seam.

`http_client=` injects a fake standing in for `x402.http.x402_http_client.
x402HTTPClientSync` so these tests never touch algosdk, algod, or the real
x402 package -- no network, no real signing, matching CLAUDE.md's "fake the
seam, never mock the network."
"""

from __future__ import annotations

import pytest
from pxke_x402 import PxkeClient
from pxke_x402.client import BASE_URL
from pxke_x402.exceptions import PxkeHTTPError, PxkeOfferValidationError, PxkePaymentError

from .conftest import FakePaymentHTTPClient, FakeResponse, FakeSession
from .conftest import valid_offer_headers as _offer_headers


def test_search_news_pays_and_returns_the_result() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(
                200, {"items": [], "settlement_tx_id": "TX123"}, headers={"payment-response": "..."}
            ),
        ]
    )
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    result = client.search_news("tinyman", limit=5)

    assert result == {"items": [], "settlement_tx_id": "TX123"}
    assert len(fake_payment.calls) == 1
    # The retry carried the payment headers the fake payment client returned.
    retry_call = session.calls[1]
    assert retry_call.headers["PAYMENT-SIGNATURE"] == "fake-signature"
    assert retry_call.method == "GET"
    assert retry_call.url == f"{BASE_URL}/api/v1/x402/news/search"
    assert retry_call.params == {"q": "tinyman", "limit": 5}


# --------------------------------------------------------------------------- #
# Sandboxed URL scan
# --------------------------------------------------------------------------- #
def test_scan_url_parses_the_402_challenge_signs_and_resends_the_same_body() -> None:
    """The unpaid POST gets a 402 whose offer is decoded from PAYMENT-REQUIRED, handed to the payment client, and the retry resends the identical JSON body with the payment header."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers(amount="10000")),
            FakeResponse(
                200,
                {
                    "source_url": "https://example.com/file.zip",
                    "risk": {"score": 0.0, "verdict": "no concerns found", "malicious": False},
                    "settlement_tx_id": "TXSCAN",
                },
                headers={"payment-response": "receipt-blob"},
            ),
        ]
    )
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    result = client.scan_url("https://example.com/file.zip")

    assert result["settlement_tx_id"] == "TXSCAN"
    assert result["risk"]["malicious"] is False
    first_call, retry_call = session.calls
    assert first_call.method == "POST"
    assert first_call.url == f"{BASE_URL}/api/v1/x402/scan/url"
    assert first_call.json_body == {"url": "https://example.com/file.zip"}
    assert first_call.params == {}  # no bypass params by default
    assert first_call.headers is None  # the unpaid request carries no payment header
    # The 402 challenge (headers + body) reached the payment client exactly once.
    assert len(fake_payment.calls) == 1
    challenge_headers, _challenge_body = fake_payment.calls[0]
    assert "payment-required" in challenge_headers
    assert retry_call.json_body == first_call.json_body
    assert retry_call.headers["PAYMENT-SIGNATURE"] == "fake-signature"


def test_scan_url_refuses_a_402_offer_that_fails_validation_before_signing() -> None:
    """The 402 challenge is decoded and checked BEFORE the payment client sees it: an over-cap amount never reaches the signer."""
    session = FakeSession([FakeResponse(402, headers=_offer_headers(amount="5000000"))])
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    with pytest.raises(PxkeOfferValidationError) as excinfo:
        client.scan_url("https://example.com/file.zip")

    assert "5000000" in str(excinfo.value)
    assert fake_payment.calls == []
    assert len(session.calls) == 1


def test_scan_url_fetch_failure_after_payment_raises_payment_error_with_settled_true() -> None:
    """A caller-supplied url that can't be fetched is charged (422, payment kept), not refunded."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(
                422,
                {
                    "error": {"code": "fetch_failed", "message": "connection refused"},
                    "settlement_tx_id": "TXKEPT",
                },
                headers={"payment-response": "receipt-blob"},
            ),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    with pytest.raises(PxkePaymentError) as excinfo:
        client.scan_url("https://down.example.com/file.zip")

    assert excinfo.value.status_code == 422
    assert excinfo.value.settled is True
    assert excinfo.value.settlement_tx_id == "TXKEPT"
    assert "connection refused" in str(excinfo.value)


def test_a_validation_error_before_the_gate_raises_http_error_and_never_pays() -> None:
    """A malformed body is a plain 4xx, not a 402 -- nothing charged, no payment built."""
    session = FakeSession(
        [
            FakeResponse(
                400,
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "url must be http:// or https://",
                    }
                },
            )
        ]
    )
    fake_payment = FakePaymentHTTPClient()
    client = PxkeClient(session=session, http_client=fake_payment)

    with pytest.raises(PxkeHTTPError) as excinfo:
        client.scan_url("ftp://example.com/file.zip")

    assert excinfo.value.status_code == 400
    assert "url must be http:// or https://" in str(excinfo.value)
    assert fake_payment.calls == []  # no payment was ever attempted
    assert len(session.calls) == 1  # no retry either


def test_a_settled_but_refused_response_raises_payment_error_with_settled_true() -> None:
    """Owner-only ownership refusal: payment settles, request is refused, nothing changes."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(
                403,
                {"error": {"code": "not_backup_owner", "message": "not your backup"}},
                headers={"payment-response": "receipt-blob"},
            ),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    with pytest.raises(PxkePaymentError) as excinfo:
        client.storage_renew_backup("b1", "OTHERWALLET")

    assert excinfo.value.status_code == 403
    assert excinfo.value.settled is True
    assert excinfo.value.settlement_tx_id is None  # not in the body for this case
    assert "settled but the request was refused" in str(excinfo.value)


def test_a_failure_building_the_payment_raises_payment_error_not_a_raw_exception() -> None:
    session = FakeSession([FakeResponse(402, headers=_offer_headers())])
    broken_payment_client = FakePaymentHTTPClient(raises=RuntimeError("bad offer encoding"))
    client = PxkeClient(session=session, http_client=broken_payment_client)

    with pytest.raises(PxkePaymentError) as excinfo:
        client.search_news("tinyman")

    assert "bad offer encoding" in str(excinfo.value)
    assert len(session.calls) == 1  # never got to retry


def test_settlement_tx_id_is_surfaced_on_success() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"risk": {"score": 0.0}, "settlement_tx_id": "TXABC"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    result = client.scan_url("https://example.com/file.zip")

    assert result["settlement_tx_id"] == "TXABC"


# --------------------------------------------------------------------------- #
# Agent backup storage
# --------------------------------------------------------------------------- #
def test_storage_create_backup_base64_encodes_data_and_declares_its_size() -> None:
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"backup_id": "b1", "settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.storage_create_backup(b"hello world", label="my backup")

    first_call, retry_call = session.calls
    assert first_call.url == f"{BASE_URL}/api/v1/x402/storage/backups"
    assert first_call.params == {"declared_size_bytes": 11}
    assert retry_call.json_body == {"data": "aGVsbG8gd29ybGQ=", "label": "my backup"}


def test_storage_renew_backup_sends_wallet_as_a_query_param_and_no_body() -> None:
    """The server looks the backup up by `?wallet=` BEFORE the 402 offer is built (it needs the stored size to price the renewal), so the wallet must travel in the query string, not the JSON body."""
    session = FakeSession(
        [
            FakeResponse(402, headers=_offer_headers()),
            FakeResponse(200, {"backup_id": "b1", "settlement_tx_id": "TX1"}),
        ]
    )
    client = PxkeClient(session=session, http_client=FakePaymentHTTPClient())

    client.storage_renew_backup("b/1", "WALLETADDR")

    first_call, retry_call = session.calls
    assert first_call.url == f"{BASE_URL}/api/v1/x402/storage/backups/b%2F1/renew"
    assert first_call.params == {"wallet": "WALLETADDR"}
    assert first_call.json_body is None
    assert retry_call.params == {"wallet": "WALLETADDR"}  # resent unchanged with the payment
