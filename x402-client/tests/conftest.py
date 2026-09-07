"""Shared fakes for pxke_x402 tests.

No real network: `FakeSession` stands in for `requests.Session` and is
scripted per-test with a list of canned responses, in call order. This is
the same "fake the seam, never mock the network" style the PXke Algorand
backend's own test suite uses (see backend/tests/test_x402_settlement.py).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class FakeResponse:
    """Just enough of a `requests.Response` for the client to read."""

    status_code: int
    json_body: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def content(self) -> bytes:
        return b"{}" if self.json_body is None else json.dumps(self.json_body).encode()

    def json(self) -> Any:
        if self.json_body is None:
            raise ValueError("no body")
        return self.json_body


@dataclass
class RecordedCall:
    method: str
    url: str
    params: dict[str, Any] | None
    json_body: dict[str, Any] | None
    headers: dict[str, Any] | None


class FakeSession:
    """Scripted `requests.Session` replacement: pop the next canned response per call."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[RecordedCall] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        del timeout
        self.calls.append(RecordedCall(method, url, params, json, headers))
        if not self._responses:
            raise AssertionError(f"FakeSession ran out of scripted responses for {method} {url}")
        return self._responses.pop(0)


def encode_payment_required_header(offer: dict[str, Any]) -> str:
    """Base64-encode a 402 offer the way the real PAYMENT-REQUIRED header does."""
    return base64.b64encode(json.dumps(offer).encode()).decode()


def valid_offer_headers(
    *, pay_to: str = "KSAVOYTVNB7A6NKCM4W2WBOOGFHWH2SEGR5T6OGB7THCAT5E36LDFEBTII", amount: str = "10000"
) -> dict[str, str]:
    """A real, decodable PAYMENT-REQUIRED header -- unlike a placeholder blob, this passes through PxkeClient._parse_payment_required (the real x402 package's decode_payment_required_header + PaymentRequired schema), so it satisfies the client's own pre-sign offer validation (recipient allowlist + amount cap, see client.py's DEFAULT_EXPECTED_PAY_TO/DEFAULT_MAX_PAYMENT_ATOMIC) by default. Pass a different pay_to/amount to specifically exercise a rejection.

    `pay_to` defaults to the real DEFAULT_EXPECTED_PAY_TO value inline (not
    imported from client.py) so a test that imports this fixture is not
    accidentally shielded from a regression in that constant itself.
    """
    offer = {
        "x402Version": 2,
        "accepts": [
            {
                "scheme": "exact",
                "network": "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=",
                "asset": "31566704",
                "amount": amount,
                "pay_to": pay_to,
                "max_timeout_seconds": 60,
            }
        ],
    }
    return {"payment-required": encode_payment_required_header(offer)}


class FakePaymentHTTPClient:
    """Fakes `x402.http.x402_http_client.x402HTTPClientSync`'s one method the client calls.

    `handle_402_response(headers, body) -> (payment_headers, payload)`. Lets
    tests exercise the 402->sign->retry flow without touching algosdk,
    algod, or the real x402 package at all.
    """

    def __init__(
        self,
        payment_headers: dict[str, str] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self.payment_headers = payment_headers or {"PAYMENT-SIGNATURE": "fake-signature"}
        self.raises = raises
        self.calls: list[tuple[dict[str, str], bytes]] = []

    def handle_402_response(
        self, headers: dict[str, str], body: bytes
    ) -> tuple[dict[str, str], Any]:
        self.calls.append((headers, body))
        if self.raises is not None:
            raise self.raises
        return self.payment_headers, object()


@pytest.fixture
def fake_payment_client() -> FakePaymentHTTPClient:
    return FakePaymentHTTPClient()
