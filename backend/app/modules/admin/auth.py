from __future__ import annotations

from robyn import Request, Response

from app.core.config import settings
from app.core.http_errors import json_error_response
from app.modules.auth.services.session_store import SessionStore

# Lazy redis client (redis.from_url does not connect until first command), so
# constructing this at import is safe.
_session_store = SessionStore()


def admin_wallet_addresses() -> set[str]:
    raw = getattr(settings, "admin_wallet_addresses", "") or ""
    return {a.strip().upper() for a in raw.split(",") if a.strip()}


def _session_wallet(request: Request) -> str:
    """Verified wallet for the request's session token, or "" if none/expired.

    The session token is minted only after a nonce + wallet-signature proof at
    sign-in, so the wallet behind it has cryptographically proven key ownership.
    """
    token = (
        request.headers.get("x-session-token")
        or request.headers.get("X-Session-Token")
        or ""
    ).strip()
    if not token:
        return ""
    rec = _session_store.get_session(token)
    if not rec:
        return ""
    return (rec.wallet_address or "").strip()


def verified_admin_wallet(request: Request) -> str:
    """The authenticated admin wallet to attribute an action to (verified, never
    the self-asserted header). Call only after require_admin_wallet has passed."""
    return _session_wallet(request)


def require_admin_wallet(request: Request) -> Response | None:
    """Authorize admin actions via the signed session, not a self-asserted header.

    A wallet address is public on-chain data: a caller putting one in a header
    proves nothing. We instead resolve the X-Session-Token to the wallet that
    proved key ownership at sign-in, and check THAT against the allowlist.
    """
    allowed = admin_wallet_addresses()
    if not allowed:
        return json_error_response(
            503,
            "admin_not_configured",
            "ADMIN_WALLET_ADDRESSES is not set on the API",
        )
    wallet = _session_wallet(request)
    if not wallet:
        return json_error_response(
            401,
            "unauthorized",
            "Authenticated admin session required (sign in, then send X-Session-Token)",
        )
    if wallet.upper() not in allowed:
        return json_error_response(403, "forbidden", "Wallet is not an admin")
    return None
