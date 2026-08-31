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
