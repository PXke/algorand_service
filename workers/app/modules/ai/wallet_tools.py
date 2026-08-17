"""connect_wallet -- the writer agent's only capability to complete a WalletConnect LOGIN using its own dedicated Algorand wallet.

Phase 1 -- login/session-proof only, see workers/app/modules/wallet/signer.py's
docstring for the security boundary this sits on top of. Kept as a SEPARATE
tool from play_interactive (not an action on it) so it gets its own budget,
its own audit trail, and keeps play_interactive's return shape untouched --
see /home/g/.claude-personal/plans/synchronous-greeting-star.md for the full
design rationale.

Registered unconditionally into all_tools() via _register_toolset; the
kill switch (AGENT_WALLET_ENABLED) is checked here at registration time, so
a disabled/unconfigured wallet doesn't even offer the tool to the model --
same pattern as INVESTIGATIVE_TOOLS_ENABLED in writer_tools.py.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Bounded to the character set an actual wc: URI uses (topic hex, version,
# and a url-encoded querystring) so this stops cleanly at the closing quote/
# tag in real markup rather than swallowing trailing HTML.
_WC_URI_RE = re.compile(r"wc:[A-Za-z0-9%\-_.@=&?:/]+")


def _discover_wc_uri(page_html: str) -> str | None:
    """Best-effort scan of a rendered page's HTML for a wc: URI (an anchor href, a data-uri attribute, or plain visible text) -- no QR-code image decoding in this phase."""
    match = _WC_URI_RE.search(html.unescape(page_html or ""))
    return match.group(0) if match else None


def _tool_connect_wallet(
    wc_uri: str = "",
    playwright_session: Any = None,  # noqa: ANN401 -- PlaywrightSession; injected from compose context, see writer_tools._wrap_connect_wallet
) -> dict[str, Any]:
    """Complete a WalletConnect login on the currently-open interactive page (or a directly-supplied wc_uri) using the agent's own wallet."""
    from app.modules.wallet import wc_session

    uri = (wc_uri or "").strip()
    if not uri:
        if playwright_session is None:
            return {
                "error": (
                    "no interactive session open -- call play_interactive with "
                    "action='open' first, or pass wc_uri directly"
                )
            }
        try:
            page_html = playwright_session.interactive_read().html
        except Exception as exc:
            return {"error": f"could not read the current interactive page: {exc}"[:200]}
        uri = _discover_wc_uri(page_html) or ""
        if not uri:
            return {
                "error": "no WalletConnect URI found on the current interactive page",
                "hint": (
                    "clicking a generic 'Connect Wallet' button is usually only step "
                    "one -- most dapps that support several wallets (Pera/Defly/Lute, "
                    "etc.) then show a PICKER, and the actual QR code/wc: URI only "
                    "renders after you click one NAMED wallet in that picker. If you've "
                    "only clicked the generic button so far, use play_interactive to "
                    "look for and click a specific wallet name/logo next, THEN retry "
                    "connect_wallet -- don't assume one click was enough just because "
                    "the page changed."
                ),
            }

    result = wc_session.complete_login(uri)
    return {
        "ok": result.ok,
        "address": result.address,
        "method": result.method,
        "error": result.error,
        "note": result.note,
    }


CONNECT_WALLET_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "connect_wallet",
        "description": (
            "Complete a WalletConnect LOGIN on the currently-open interactive page "
            "(requires play_interactive action='open' first) using the agent's own "
            "dedicated Algorand wallet. Approves the wallet-connect session, then answers "
            "at most one follow-up signing request if the dapp sends one -- login/"
            "session-proof only. Many dapps need nothing further than the session "
            "approval itself (ok=true, method=null is a normal, successful outcome, not "
            "a partial failure -- check the note field). This tool can NEVER move value: "
            "any signing request for a transaction that isn't an exact 0-ALGO self-payment "
            "on MainNet, or any request type other than a login signature, is automatically "
            "declined. Use this to get past a 'Connect your wallet' gate and see what's "
            "behind it -- not to opt into an asset, buy/sell, or send a payment; none of "
            "that is possible with this tool.\n\n"
            "If the page shows a WalletConnect QR code/link, this tool discovers the "
            "wc: URI from the page automatically -- pass wc_uri only if you've already "
            "seen the literal wc:... string in the page's own text.\n\n"
            "IMPORTANT for multi-wallet dapps: clicking a generic 'Connect Wallet' "
            "button is usually only step one and just opens a PICKER (Pera/Defly/"
            "Lute logos or names) -- the QR/wc: URI itself only appears after you "
            "click one SPECIFIC named wallet in that picker. Calling this tool right "
            "after the generic click, with no QR/URI actually on screen yet, will "
            "just fail with 'no WalletConnect URI found' -- click a named wallet "
            "option first if the page still looks like a picker/menu rather than a "
            "QR code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "wc_uri": {
                    "type": "string",
                    "description": (
                        "optional -- a wc:... URI already seen in the page text; "
                        "omit to auto-discover it from the current interactive page"
                    ),
                },
            },
        },
    },
}

WALLET_SCHEMAS: list[dict[str, Any]] = [CONNECT_WALLET_SCHEMA]
WALLET_HANDLERS: dict[str, Any] = {"connect_wallet": _tool_connect_wallet}


def wallet_tools() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """connect_wallet (schema, handler), or empty when AGENT_WALLET_ENABLED is off."""
    from app.core.config import AGENT_WALLET_ENABLED

    if not AGENT_WALLET_ENABLED:
        return [], {}
    return list(WALLET_SCHEMAS), dict(WALLET_HANDLERS)
