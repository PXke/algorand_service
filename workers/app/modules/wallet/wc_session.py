"""WalletConnect transport for the agent wallet (Phase 1: login only).

Thin wrapper around pyWalletConnect's WCClient (the only Python library found
that implements the WALLET/responder side of the protocol -- everything else
in this repo, TS @perawallet/connect and Dart ensemble_walletconnect, is
dapp-side). WCv1Client and WCv2Client (returned interchangeably by
WCClient.from_wc_uri) share the same public surface used here:
open_session() / reply_session_request() / reject_session_request() /
get_message() / reply() / reply_error().

SSRF: pyWalletConnect opens a raw WebSocket to the URI's bridge/relay host,
bypassing every other guarded-fetch path in this codebase (net_guard's
assert_public_url normally covers exactly this class of "a URL we did not
author"). A WalletConnect v1 URI carries its bridge host directly in the URI
(`bridge=<url>`) -- DOM-scraped from a page we're investigating, so
untrusted -- and is guarded here BEFORE from_wc_uri() ever opens a
connection. v2 URIs carry no such field; pyWalletConnect connects to
WalletConnect Foundation's own fixed official relay in that case, which is
not attacker-supplied, so there is nothing to guard.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

from app.modules.wallet import signer

logger = logging.getLogger(__name__)

# Session-approval metadata only (not a security boundary -- see signer.py's
# module docstring for what actually is). Algorand WalletConnect v1's
# conventional numeric chain id for MainNet (matches deploy.sh's own
# ALGORAND_NETWORK=mainnet -> _CHAIN_ID_DEFAULT mapping) -- this wallet holds
# real MainNet ALGO, see signer.py's module docstring.
_MAINNET_CHAIN_ID = 416001

_MESSAGE_POLL_INTERVAL_SECONDS = 0.5
_MESSAGE_WAIT_TIMEOUT_SECONDS = 25.0


@dataclass(frozen=True)
class WalletConnectResult:
    """Outcome of one connect_wallet attempt."""

    ok: bool
    address: str | None = None
    method: str | None = None
    error: str | None = None
    note: str | None = None


def _extract_v1_bridge_host(wc_uri: str) -> str | None:
    """The `bridge` query param of a WalletConnect v1 URI, or None (not present -- v2, or malformed)."""
    if "?" not in wc_uri:
        return None
    _, _, query = wc_uri.partition("?")
    values = parse_qs(query).get("bridge")
    return values[0] if values else None


def complete_login(wc_uri: str) -> WalletConnectResult:
    """Connect to `wc_uri`, approve the session with the agent's own address, answer exactly one incoming signing request through signer.handle_request, then close. Never raises."""
    uri = (wc_uri or "").strip()
    if not uri.startswith("wc:"):
        return WalletConnectResult(ok=False, error="not a WalletConnect URI (must start with wc:)")

    address = signer.agent_wallet_address()
    if address is None:
        return WalletConnectResult(ok=False, error="agent wallet not configured")

    bridge = _extract_v1_bridge_host(uri)
    if bridge:
        try:
            from app.core.net_guard import assert_public_url

            assert_public_url(bridge)
        except Exception as exc:
            logger.warning("rejecting WalletConnect URI with unsafe bridge host: %s", exc)
            return WalletConnectResult(ok=False, error=f"unsafe bridge host: {exc}")

    client = None
    try:
        from pywalletconnect.client import WCClient

        client = WCClient.from_wc_uri(uri)
        msg_id, _chain_ids, _peer_meta = client.open_session()
        client.reply_session_request(msg_id, _MAINNET_CHAIN_ID, address)

        req_id, method, params = _wait_for_request(client)
        if req_id is None:
            # Root-caused live 2026-08-11 against lumirogue.com: the session
            # approval above IS the login -- plenty of real dapps only need
            # the address from reply_session_request and never follow up
            # with a signing request at all (confirmed: the dapp displayed
            # our real address and gated content on it, with zero further
            # WalletConnect traffic). Treat this as a successful, signature-
            # less connection, not a failure.
            return WalletConnectResult(
                ok=True,
                address=address,
                note="session approved; dapp did not request a signature",
            )

        decision = signer.handle_request(method, params)
        if decision.approved:
            client.reply(req_id, decision.result)
            return WalletConnectResult(ok=True, address=address, method=method)
        client.reply_error(req_id, decision.decline_reason or "declined", 4001)
        return WalletConnectResult(
            ok=False, address=address, method=method, error=decision.decline_reason
        )
    except Exception as exc:
        logger.warning("WalletConnect login attempt failed", exc_info=True)
        return WalletConnectResult(ok=False, address=address, error=str(exc)[:200])
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.debug("WalletConnect client close failed", exc_info=True)


def _wait_for_request(client: Any) -> tuple[int | None, str, Any]:  # noqa: ANN401 -- pyWalletConnect WCv1Client|WCv2Client
    """Poll get_message() (non-blocking) until one request arrives or the timeout elapses."""
    deadline = time.monotonic() + _MESSAGE_WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        req_id, method, params = client.get_message()
        if req_id is not None:
            return req_id, method, params
        time.sleep(_MESSAGE_POLL_INTERVAL_SECONDS)
    return None, "", []
