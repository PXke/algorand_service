"""Usage-proof-of-payment for grading: txid shape, on-chain verification, live payTo fetch.

Fully offline: httpx.Client is monkeypatched to a MockTransport (never a real
socket) wherever a request would otherwise leave the process, and
_resolve_public_ip is monkeypatched the same way test_media_routes.py already
does for the shared SSRF-pinning helper this module reuses.
"""

from __future__ import annotations

import base64
import json
from typing import Never

import httpx
import pytest

from app.modules.x402_grading.services import usage_proof

_TXID = "A" * 52
_PAYER = "P" * 58
# A real, checksum-valid Algorand address (algosdk.account.generate_account())
# -- fetch_live_payto's parsing runs a real is_valid_address check, unlike
# verify_onchain_payment's plain string comparison, so a placeholder like
# "E" * 58 would fail validation and every payTo-parsing test would see None.
_ENDPOINT_PAYTO = "ZN65GDXA2QUSUNWM57SPAAVDQZ2I7KX2HLYHTH2EJCKKQGNKMU26JGJEPA"
_RealClient = httpx.Client


def _patch_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _RealClient(transport=transport, **kwargs)
    )


# --------------------------------------------------------------------------- #
# validate_txid_shape
# --------------------------------------------------------------------------- #
def test_a_well_formed_txid_is_accepted_and_uppercased() -> None:
    """A 52-char base32 txid round-trips uppercased."""
    assert usage_proof.validate_txid_shape("a" * 52) == "A" * 52


@pytest.mark.parametrize(
    "raw",
    ["", "A" * 51, "A" * 53, "0" * 52, "A" * 51 + "1", " " * 52],
)
def test_a_malformed_txid_is_rejected(raw: str) -> None:
    """Wrong length, invalid base32 chars, or blank -- all rejected before any network call."""
    with pytest.raises(usage_proof.UsageProofError):
        usage_proof.validate_txid_shape(raw)


# --------------------------------------------------------------------------- #
# verify_onchain_payment
# --------------------------------------------------------------------------- #
def test_verify_onchain_payment_confirms_a_matching_payment_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confirmed payment-transaction whose receiver matches expected_payto verifies True."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "transaction": {
                    "sender": _PAYER,
                    "payment-transaction": {"receiver": _ENDPOINT_PAYTO},
                }
            },
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    result = usage_proof.verify_onchain_payment(tx_id=_TXID, expected_payto=_ENDPOINT_PAYTO)

    assert result.verified is True
    assert result.actual_sender == _PAYER


def test_verify_onchain_payment_confirms_a_matching_asset_transfer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """USDC/ASA payments are asset-transfer-transactions, not payment-transactions -- both are checked."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "transaction": {
                    "sender": _PAYER,
                    "asset-transfer-transaction": {"receiver": _ENDPOINT_PAYTO},
                }
            },
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    result = usage_proof.verify_onchain_payment(tx_id=_TXID, expected_payto=_ENDPOINT_PAYTO)

    assert result.verified is True
    assert result.actual_sender == _PAYER


def test_verify_onchain_payment_rejects_a_wrong_receiver(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real, confirmed transaction that paid someone else is a definite False, not None."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "transaction": {
                    "sender": _PAYER,
                    "payment-transaction": {"receiver": "W" * 58},
                }
            },
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    result = usage_proof.verify_onchain_payment(tx_id=_TXID, expected_payto=_ENDPOINT_PAYTO)

    assert result.verified is False
    assert "receiver" in result.detail
    # actual_sender still travels, so a caller could still identify who it was.
    assert result.actual_sender == _PAYER


def test_verify_onchain_payment_reports_a_missing_transaction_as_false_not_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404 from the indexer is a confirmed absence (False), distinct from an unreachable indexer (None)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "no such transaction"})

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    result = usage_proof.verify_onchain_payment(tx_id=_TXID, expected_payto=_ENDPOINT_PAYTO)

    assert result.verified is False
    assert "no confirmed transaction" in result.detail


def test_verify_onchain_payment_reports_an_unreachable_indexer_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Undeterminable (network failure) must never collapse into False -- CLAUDE.md invariant 8."""

    def _boom(**_kwargs: object) -> Never:
        raise httpx.ConnectError("no network", request=None)

    monkeypatch.setattr(httpx, "Client", _boom)

    result = usage_proof.verify_onchain_payment(tx_id=_TXID, expected_payto=_ENDPOINT_PAYTO)

    assert result.verified is None


# --------------------------------------------------------------------------- #
# fetch_live_payto
# --------------------------------------------------------------------------- #
def _offer_header(payto: str) -> str:
    return base64.b64encode(json.dumps({"accepts": [{"payTo": payto}]}).encode()).decode()


def test_fetch_live_payto_reads_the_header_encoded_offer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The payment-required header, base64+JSON encoded, is the preferred offer source."""
    monkeypatch.setattr(usage_proof, "_resolve_public_ip", lambda _host: "203.0.113.5")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, headers={"payment-required": _offer_header(_ENDPOINT_PAYTO)})

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    assert usage_proof.fetch_live_payto("https://example.com/api") == _ENDPOINT_PAYTO


def test_fetch_live_payto_falls_back_to_the_json_body_offer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No header -- the JSON response body with an `accepts` list is the fallback source."""
    monkeypatch.setattr(usage_proof, "_resolve_public_ip", lambda _host: "203.0.113.5")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"accepts": [{"pay_to": _ENDPOINT_PAYTO}]})

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    assert usage_proof.fetch_live_payto("https://example.com/api") == _ENDPOINT_PAYTO


def test_fetch_live_payto_retries_as_post_on_405(monkeypatch: pytest.MonkeyPatch) -> None:
    """Many x402 resources only answer POST, same reasoning as the workers probe."""
    monkeypatch.setattr(usage_proof, "_resolve_public_ip", lambda _host: "203.0.113.5")
    seen_methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(405)
        return httpx.Response(402, headers={"payment-required": _offer_header(_ENDPOINT_PAYTO)})

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    assert usage_proof.fetch_live_payto("https://example.com/api") == _ENDPOINT_PAYTO
    assert seen_methods == ["GET", "POST"]


def test_fetch_live_payto_returns_none_for_a_non_public_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSRF guard: a host that does not resolve publicly is refused before any request."""
    monkeypatch.setattr(usage_proof, "_resolve_public_ip", lambda _host: None)

    assert usage_proof.fetch_live_payto("https://internal.example/api") is None


def test_fetch_live_payto_returns_none_for_a_non_402_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target that never answers 402 has no offer to read a payTo from."""
    monkeypatch.setattr(usage_proof, "_resolve_public_ip", lambda _host: "203.0.113.5")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    assert usage_proof.fetch_live_payto("https://example.com/api") is None


def test_fetch_live_payto_returns_none_on_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails closed on this specific value (returns None), never raises."""
    monkeypatch.setattr(usage_proof, "_resolve_public_ip", lambda _host: "203.0.113.5")

    def _boom(**_kwargs: object) -> Never:
        raise httpx.ConnectError("no network", request=None)

    monkeypatch.setattr(httpx, "Client", _boom)

    assert usage_proof.fetch_live_payto("https://example.com/api") is None
