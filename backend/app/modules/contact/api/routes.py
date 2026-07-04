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

from robyn import Request, Response

from app.core import serialization
from app.core.http_errors import json_error_response
from app.modules.admin.auth import require_admin_wallet
from app.modules.contact.store import insert_message, list_recent
from app.schemas import ContactMessageRequest

logger = logging.getLogger(__name__)

_RATE_LIMIT_PER_HOUR = 5


def _client_ip(request: Request) -> str:
    raw = (
        request.headers.get("x-forwarded-for")
        or request.headers.get("x-real-ip")
        or ""
    )
    return raw.split(",")[0].strip()


@lru_cache(maxsize=1)
def _redis():
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


def register_contact_routes(app) -> None:
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
