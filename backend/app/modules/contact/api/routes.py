"""Public contact form + admin inbox.

The form is unauthenticated by design (a reader without a wallet must be able
to reach us), so it carries two cheap abuse gates: a honeypot field bots fill
and humans never see, and a per-IP hourly rate limit in Redis. There is no
outbound e-mail anywhere in the stack — messages are read from the admin UI.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis

from app.core import serialization
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.modules.admin.auth import require_admin_wallet
from app.modules.contact.store import insert_message, list_recent
from app.schemas import ContactMessageRequest

logger = logging.getLogger(__name__)

_RATE_LIMIT_PER_HOUR = 5


def _client_ip(request: Request) -> str:
    """Real client IP for rate-limiting, resistant to header spoofing.

    Trust X-Real-IP first: nginx sets it from $remote_addr and overwrites any
    client-supplied value (see deploy/nginx). X-Forwarded-For is NOT safe to
    read left-to-right — nginx's proxy_add_x_forwarded_for prepends the client's
    own XFF, so its first element is attacker-controlled and would hand every
    spoofed value its own rate-limit bucket. Fall back to the LAST XFF hop (the
    one appended by our proxy) only when X-Real-IP is absent (e.g. local dev).
    """
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    xff = request.headers.get("x-forwarded-for") or ""
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    return parts[-1] if parts else ""


@lru_cache(maxsize=1)
def _redis() -> redis.Redis:
    import redis

    from app.core.config import settings

    return redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)


def _rate_limited(ip: str) -> bool:
    """Fail OPEN: a Redis hiccup must never block a reader's message."""
    if not ip:
        return False
    with suppress(Exception):
        key = f"algorand:contact:rl:{ip}"
        client = _redis()
        count = client.incr(key)
        if count == 1:
            client.expire(key, 3600)
        return int(count) > _RATE_LIMIT_PER_HOUR
    return False


def register_contact_routes(app: Router) -> None:
    """Wire the public contact-submit and admin contact-inbox routes onto app."""

    @app.post("/api/v1/contact")
    async def contact_submit(request: Request) -> Response | dict:
        try:
            payload = serialization.decode(request.body, ContactMessageRequest)
        except serialization.DecodeError as exc:
            return json_error_response(400, "invalid_request", str(exc))

        # Honeypot tripped: answer success so the bot learns nothing, store nothing.
        if payload.website.strip():
            return {"ok": True}

        if await asyncio.to_thread(_rate_limited, _client_ip(request)):
            return json_error_response(
                429, "rate_limited", "Too many messages — please try again later"
            )

        await asyncio.to_thread(
            insert_message,
            name=payload.name.strip(),
            email=payload.email.strip(),
            message=payload.message.strip(),
        )
        return {"ok": True}

    @app.get("/api/v1/admin/contact-messages")
    async def admin_contact_messages(request: Request) -> Response | dict:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        items = await asyncio.to_thread(list_recent)
        return {"items": serialization.to_builtins(items)}
