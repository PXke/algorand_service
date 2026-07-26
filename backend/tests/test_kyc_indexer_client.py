"""Wallet-signal fetching from the indexer, including its fail-open paths."""

from __future__ import annotations

from typing import Never

import httpx
import pytest

from app.modules.kyc.services.indexer_client import fetch_wallet_signals

WALLET = "W" * 58


_RealClient = httpx.Client


def _patch_client_with_transport(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport
) -> None:
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _RealClient(transport=transport, **kwargs)
    )


def _mock_transport(account_payload: dict, txns_payload: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/transactions"):
            return httpx.Response(200, json=txns_payload)
        return httpx.Response(200, json=account_payload)

    return httpx.MockTransport(handler)


def test_fetch_wallet_signals_parses_age_and_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parses wallet creation round and recent transaction count from indexer responses."""
    transport = _mock_transport(
        account_payload={"created-at-round": 12345},
        txns_payload={"transactions": [{}, {}, {}]},
    )
    _patch_client_with_transport(monkeypatch, transport)

    signals = fetch_wallet_signals(WALLET)

    assert signals.wallet_age_round == 12345
    assert signals.recent_tx_count == 3


def test_fetch_wallet_signals_fails_open_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns neutral (None/0) signals instead of raising when the indexer client can't connect."""

    def _boom(**_kwargs: object) -> Never:
        raise httpx.ConnectError("no network", request=None)

    monkeypatch.setattr(httpx, "Client", _boom)

    signals = fetch_wallet_signals(WALLET)

    assert signals.wallet_age_round is None
    assert signals.recent_tx_count == 0


def test_fetch_wallet_signals_fails_open_on_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns neutral (None/0) signals when the indexer response shape is unexpected."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    _patch_client_with_transport(monkeypatch, httpx.MockTransport(handler))

    signals = fetch_wallet_signals(WALLET)

    assert signals.wallet_age_round is None
    assert signals.recent_tx_count == 0


def test_fetch_wallet_signals_keeps_age_when_only_txn_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keeps the wallet-age signal from a successful account call even when the txn call errors."""

    # A partial failure (account lookup ok, txn lookup errors) must not throw
    # away the wallet_age signal it already had — same try/except wraps both
    # calls, but local var assignments persist across the exception.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/transactions"):
            raise httpx.ConnectError("no network", request=request)
        return httpx.Response(200, json={"created-at-round": 999})

    _patch_client_with_transport(monkeypatch, httpx.MockTransport(handler))

    signals = fetch_wallet_signals(WALLET)

    assert signals.wallet_age_round == 999
    assert signals.recent_tx_count == 0
