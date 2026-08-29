"""x402 KYC ping endpoint tests.

The 402-without-payment path runs fully offline: a stub facilitator client
avoids any real network call, and asserts verify()/settle() are never reached
when no payment header is present. The happy-path round trip needs a funded
Algorand TestNet payer account and a reachable facilitator, so it's written
but skipped by default (see the plan/memory: testnet account setup was
explicitly deferred). It has not been run end-to-end yet — treat it as a
starting point, not a verified reference, if it needs fixing later.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("x402")

from typing import Never

from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2
from x402.schemas.payments import PaymentRequirements
from x402.schemas.responses import SupportedKind, SupportedResponse
from x402.schemas.v1 import PaymentRequirementsV1
from x402.server import x402ResourceServerSync

from app.core.config import settings
from app.core.http import QueryParams, Request
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard


def _fake_request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        method="GET",
        headers=headers or {},
        query_params=QueryParams(),
        path_params={},
        body=b"",
        url=SimpleNamespace(scheme="http", host="localhost", path="/api/v1/kyc/_test/ping"),
    )


class _StubFacilitator:
    """Canned /supported response, no network. verify()/settle() raise if called — the no-payment-header path must never reach either."""

    def get_supported(self) -> SupportedResponse:
        return SupportedResponse(
            kinds=[SupportedKind(x402_version=2, scheme="exact", network=ALGORAND_TESTNET_CAIP2)]
        )

    def verify(
        self, _payload: dict, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("verify() must not be called without a payment header")

    def settle(
        self, _payload: dict, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("settle() must not be called without a payment header")


def _stub_resource_server() -> x402ResourceServerSync:
    server = x402ResourceServerSync(_StubFacilitator())
    x402_client.register_tagged_exact_avm_scheme(server, ALGORAND_TESTNET_CAIP2)
    server.initialize()
    return server


def test_require_payment_returns_402_without_payment_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns a 402 with a PAYMENT-REQUIRED header and no payer when no payment header is sent."""
    monkeypatch.setattr(x402_guard, "get_resource_server", _stub_resource_server)
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", "A" * 58)

    result = x402_guard.require_payment(_fake_request(), price="$0.01", resource="kyc-ping")

    assert result.error is not None
    assert result.error.status_code == 402
    assert "PAYMENT-REQUIRED" in result.error.headers
    assert result.payer is None


def test_require_payment_always_carries_the_challenge_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real 402 response's payment option carries the contest challenge tag in its extra field."""
    # Required by the contest submission checklist: every route must carry
    # this tag or it doesn't qualify for the leaderboard. Asserted against the
    # REAL 402 response the caller receives (not just constructor args) —
    # PaymentOption.extra does NOT reach this response in the installed
    # package (verified by reading server_base.py), so this exercises the
    # actual mechanism: a custom money parser registered on the AVM scheme
    # (see client.py's register_tagged_exact_avm_scheme/CHALLENGE_TAG).
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(x402_guard, "get_resource_server", _stub_resource_server)
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", "A" * 58)

    result = x402_guard.require_payment(_fake_request(), price="$0.01", resource="kyc-ping")

    payment_required = decode_payment_required_header(result.error.headers["PAYMENT-REQUIRED"])
    assert payment_required.accepts[0].extra["tag"] == x402_client.CHALLENGE_TAG


def test_tagged_money_parser_matches_default_usdc_conversion(
    monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001 -- name must match the real callee's keyword arg
) -> None:
    """The tagged money parser produces the same amount/asset as the package's default parser, plus the tag."""
    # The tag must not change the actual payment math — same amount/asset as
    # the package's own default parser would produce for the same price.
    from x402.mechanisms.avm.utils import get_usdc_asa_id, to_atomic_amount

    tagged = x402_client._tagged_money_parser(0.01, ALGORAND_TESTNET_CAIP2)
    assert tagged.amount == str(to_atomic_amount(0.01, 6))
    assert tagged.asset == str(get_usdc_asa_id(ALGORAND_TESTNET_CAIP2))
    assert tagged.extra["tag"] == x402_client.CHALLENGE_TAG


def test_require_payment_declares_bazaar_discovery_when_extensions_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 402 response declares a bazaar discovery extension when one is passed to require_payment."""
    from x402.extensions.bazaar import declare_discovery_extension
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(x402_guard, "get_resource_server", _stub_resource_server)
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", "A" * 58)

    result = x402_guard.require_payment(
        _fake_request(),
        price="$0.01",
        resource="kyc-ping",
        extensions=declare_discovery_extension(
            input={"wallet": "ABC..."},
            input_schema={"properties": {"wallet": {"type": "string"}}, "required": ["wallet"]},
        ),
    )

    payment_required = decode_payment_required_header(result.error.headers["PAYMENT-REQUIRED"])
    assert "bazaar" in (payment_required.extensions or {})


def test_require_payment_has_no_bazaar_declaration_when_extensions_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 402 response has no bazaar extension when require_payment is called without extensions."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(x402_guard, "get_resource_server", _stub_resource_server)
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", "A" * 58)

    result = x402_guard.require_payment(_fake_request(), price="$0.01", resource="kyc-ping")

    payment_required = decode_payment_required_header(result.error.headers["PAYMENT-REQUIRED"])
    assert not (payment_required.extensions or {}).get("bazaar")


def test_kyc_routes_declare_discovery_extension_without_raising() -> None:
    """Regression: both KYC paid routes built their Bazaar discovery extension with a real AttributeError before this fix -- output={"example": ...} is a plain dict, but the installed package reads output.example as an attribute, not a dict key. Both routes now go through describe_json_endpoint (modules/x402/discovery.py), which cannot reproduce this bug -- it always wraps in OutputConfig. Calling it with the exact shape each route uses must not raise."""
    from app.modules.x402.discovery import describe_json_endpoint

    # Same shape as kyc_test_ping's extensions= argument.
    describe_json_endpoint(input={}, input_schema={}, output_example={"ok": True, "paid_by": "..."})
    # Same shape as kyc_verify's extensions= argument.
    describe_json_endpoint(
        input={"wallet": "ALGORAND_ADDRESS"},
        input_schema={"properties": {"wallet": {"type": "string"}}, "required": ["wallet"]},
        output_example={
            "enrolled": True,
            "wallet_address": "...",
            "kyc_level": "basic",
            "payout_status": "sent",
        },
    )


@pytest.mark.skipif(
    os.environ.get("X402_TESTNET_INTEGRATION") != "1",
    reason="needs a funded Algorand TestNet payer + reachable facilitator "
    "(set X402_TESTNET_INTEGRATION=1, X402_TEST_PAYER_MNEMONIC, "
    "and run the backend with X402_ENABLED=true against X402_TEST_BASE_URL)",
)
def test_kyc_ping_round_trip_on_testnet() -> None:
    """Full 402 -> pay -> verify -> settle round trip against the real GoPlausible facilitator on TestNet. Written against x402-avm==2.0.2 source (2026-07-13) but not yet executed — no funded TestNet account existed at write time. Re-check the lower-level calls here against the installed package first if this fails."""
    import base64

    import httpx
    from algosdk import encoding, mnemonic
    from x402.http.utils import decode_payment_required_header, encode_payment_signature_header
    from x402.mechanisms.avm.exact.client import ExactAvmScheme
    from x402.schemas.payments import PaymentPayload

    payer_mnemonic = os.environ["X402_TEST_PAYER_MNEMONIC"]
    base_url = os.environ.get("X402_TEST_BASE_URL", "http://127.0.0.1:8080")
    private_key = mnemonic.to_private_key(payer_mnemonic)
    address = mnemonic.to_public_key(payer_mnemonic)

    class _MnemonicSigner:
        @property
        def address(self) -> str:
            return address

        def sign_transactions(
            self, unsigned_txns: list[bytes], indexes_to_sign: list[int]
        ) -> list[bytes | None]:
            out: list[bytes | None] = []
            for i, txn_bytes in enumerate(unsigned_txns):
                if i not in indexes_to_sign:
                    out.append(None)
                    continue
                txn_obj = encoding.msgpack_decode(base64.b64encode(txn_bytes).decode())
                signed = txn_obj.sign(private_key)
                out.append(base64.b64decode(encoding.msgpack_encode(signed)))
            return out

    with httpx.Client(timeout=30.0) as client:
        first = client.get(f"{base_url}/api/v1/kyc/_test/ping")
        assert first.status_code == 402
        payment_required = decode_payment_required_header(first.headers["PAYMENT-REQUIRED"])
        requirements = payment_required.accepts[0]

        scheme = ExactAvmScheme(_MnemonicSigner())
        inner_payload = scheme.create_payment_payload(requirements)
        payload = PaymentPayload(payload=inner_payload, accepted=requirements)
        header = encode_payment_signature_header(payload)

        second = client.get(
            f"{base_url}/api/v1/kyc/_test/ping",
            headers={"PAYMENT-SIGNATURE": header},
        )
        assert second.status_code == 200
        body = second.json()
        assert body["ok"] is True
        assert body["paid_by"] == address
