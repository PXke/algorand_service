"""inspect_network_hosts: mainnet-vs-testnet ground truth from a page's ACTUAL network requests, not its rendered/bundle text -- root-caused live 2026-08-13 when lumirogue.com's own UI copy said "Algorand Testnet" while its wallet code was hardcoded to mainnet."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.ai.research_tools import _tool_inspect_network_hosts
from app.modules.scraper.core.browser_scrape import _classify_network_hosts


def test_classify_detects_mainnet_only() -> None:
    """A page that only talks to mainnet-* hosts is classified mainnet."""
    result = _classify_network_hosts({"mainnet-api.4160.nodely.dev", "lumirogue.com"})
    assert result["detected_network"] == "mainnet"
    assert result["mainnet_hosts"] == ["mainnet-api.4160.nodely.dev"]
    assert result["testnet_hosts"] == []


def test_classify_detects_testnet_only() -> None:
    """A page that only talks to testnet-* hosts is classified testnet."""
    result = _classify_network_hosts({"testnet-idx.algonode.cloud"})
    assert result["detected_network"] == "testnet"
    assert result["testnet_hosts"] == ["testnet-idx.algonode.cloud"]


def test_classify_ambiguous_when_both_seen() -> None:
    """Both mainnet and testnet hosts observed (e.g. a wallet library's own dual config) -- reported as ambiguous, not guessed."""
    result = _classify_network_hosts({"mainnet-api.algonode.cloud", "testnet-api.algonode.cloud"})
    assert result["detected_network"] == "ambiguous"


def test_classify_unknown_when_no_recognized_host() -> None:
    """No algod/indexer-shaped hostname observed at all -- unknown, not a guess."""
    result = _classify_network_hosts({"fonts.googleapis.com", "lumirogue.com"})
    assert result["detected_network"] == "unknown"
    assert result["mainnet_hosts"] == []
    assert result["testnet_hosts"] == []


def test_classify_is_case_insensitive() -> None:
    """Hostname matching doesn't depend on case."""
    result = _classify_network_hosts({"MAINNET-API.Algonode.Cloud"})
    assert result["detected_network"] == "mainnet"


def test_inspect_network_hosts_requires_url() -> None:
    """An empty url is a usage error, not a browser launch."""
    result = _tool_inspect_network_hosts("")
    assert "error" in result


def test_inspect_network_hosts_requires_a_session() -> None:
    """No Playwright session available for this compose -- a clear usage error, not a crash."""
    result = _tool_inspect_network_hosts("https://example.com")
    assert "error" in result
    assert "Playwright" in result["error"]


def test_inspect_network_hosts_returns_session_result(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ARG001
    """Happy path: the session's classification result passes straight through to the tool caller."""
    session = MagicMock()
    session.inspect_network_hosts.return_value = {
        "detected_network": "mainnet",
        "mainnet_hosts": ["mainnet-api.4160.nodely.dev"],
        "testnet_hosts": [],
        "all_hosts": ["mainnet-api.4160.nodely.dev", "lumirogue.com"],
        "url": "https://lumirogue.com",
        "clicked": None,
    }
    result = _tool_inspect_network_hosts("lumirogue.com", playwright_session=session)
    assert result["detected_network"] == "mainnet"
    session.inspect_network_hosts.assert_called_once_with("https://lumirogue.com", click_text="")


def test_inspect_network_hosts_prepends_https_when_scheme_missing(
    monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001
) -> None:
    """A bare domain (no scheme) is upgraded to https:// before being passed to the session."""
    session = MagicMock()
    session.inspect_network_hosts.return_value = {"detected_network": "unknown"}
    _tool_inspect_network_hosts("lumirogue.com", playwright_session=session)
    session.inspect_network_hosts.assert_called_once_with("https://lumirogue.com", click_text="")


def test_inspect_network_hosts_passes_click_text_through(
    monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001
) -> None:
    """click_text is forwarded verbatim so a network call gated behind an interaction (e.g. Connect Wallet) can be triggered."""
    session = MagicMock()
    session.inspect_network_hosts.return_value = {"detected_network": "mainnet"}
    _tool_inspect_network_hosts(
        "https://example.com", click_text="Connect Wallet", playwright_session=session
    )
    session.inspect_network_hosts.assert_called_once_with(
        "https://example.com", click_text="Connect Wallet"
    )


def test_inspect_network_hosts_surfaces_session_error(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ARG001
    """A session failure (navigation timeout etc.) is reported as a tool error, not an unhandled exception."""
    session = MagicMock()
    session.inspect_network_hosts.side_effect = RuntimeError("navigation timeout")
    result = _tool_inspect_network_hosts("https://example.com", playwright_session=session)
    assert "error" in result


def test_inspect_network_hosts_tool_registered() -> None:
    """Registers inspect_network_hosts in both the tool schemas and handlers."""
    from app.modules.ai.research_tools import research_tools

    schemas, handlers = research_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "inspect_network_hosts" in names
    assert "inspect_network_hosts" in handlers
