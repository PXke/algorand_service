"""wc_session's SSRF guard: a WalletConnect v1 URI's bridge host is a DOM-scraped, un-authored URL exactly like anything else net_guard covers, and must be rejected before pyWalletConnect ever opens a socket to it."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.modules.wallet import wc_session


def test_extract_v1_bridge_host_reads_bridge_param() -> None:
    """A v1 URI's bridge= query param is decoded back to a plain URL."""
    uri = "wc:abc123@1?bridge=https%3A%2F%2Fbridge.example.com&key=deadbeef"
    assert wc_session._extract_v1_bridge_host(uri) == "https://bridge.example.com"


def test_extract_v1_bridge_host_none_for_v2_uri() -> None:
    """A v2 URI carries no bridge param -- nothing to guard, so this returns None rather than misparsing."""
    uri = "wc:abc123@2?relay-protocol=irn&symKey=deadbeef"
    assert wc_session._extract_v1_bridge_host(uri) is None


def test_complete_login_rejects_private_bridge_host_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A URI whose bridge host is a private/internal address is rejected before pyWalletConnect ever opens a socket to it."""
    from algosdk import account, mnemonic

    sk, _addr = account.generate_account()
    monkeypatch.setattr("app.core.config.AGENT_WALLET_MNEMONIC", mnemonic.from_private_key(sk))

    uri = "wc:abc123@1?bridge=http%3A%2F%2F127.0.0.1%3A6379&key=deadbeef"
    with patch("pywalletconnect.client.WCClient.from_wc_uri") as from_uri:
        result = wc_session.complete_login(uri)
    from_uri.assert_not_called()
    assert not result.ok
    assert "unsafe bridge host" in (result.error or "")


def test_complete_login_rejects_non_wc_uri() -> None:
    """Anything not starting with wc: is rejected up front, never handed to the WalletConnect client."""
    result = wc_session.complete_login("https://example.com")
    assert not result.ok
    assert "WalletConnect URI" in (result.error or "")


def test_complete_login_declines_cleanly_when_wallet_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No AGENT_WALLET_MNEMONIC -- a clean decline before ever touching the network, not a crash."""
    monkeypatch.setattr("app.core.config.AGENT_WALLET_MNEMONIC", "")
    with patch("pywalletconnect.client.WCClient.from_wc_uri") as from_uri:
        result = wc_session.complete_login("wc:abc123@2?relay-protocol=irn&symKey=deadbeef")
    from_uri.assert_not_called()
    assert not result.ok
    assert "not configured" in (result.error or "")


def test_complete_login_succeeds_when_dapp_never_requests_a_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-caused live 2026-08-11 against lumirogue.com: a real dapp can complete its own login using only the address from session approval, with no follow-up signing request ever sent. That's success, not a timeout failure."""
    from unittest.mock import MagicMock

    from algosdk import account, mnemonic

    sk, _addr = account.generate_account()
    monkeypatch.setattr("app.core.config.AGENT_WALLET_MNEMONIC", mnemonic.from_private_key(sk))
    monkeypatch.setattr(wc_session, "_MESSAGE_WAIT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(wc_session, "_MESSAGE_POLL_INTERVAL_SECONDS", 0.01)

    fake_client = MagicMock()
    fake_client.open_session.return_value = (1, [], {})
    fake_client.get_message.return_value = (None, "", [])
    with patch("pywalletconnect.client.WCClient.from_wc_uri", return_value=fake_client):
        result = wc_session.complete_login("wc:abc123@2?relay-protocol=irn&symKey=deadbeef")

    assert result.ok
    assert result.address is not None
    assert result.method is None
    assert result.error is None
    assert "did not request a signature" in (result.note or "")
    fake_client.reply_session_request.assert_called_once()
    fake_client.close.assert_called_once()
