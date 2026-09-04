"""Public, token-stripping reverse proxy onto the operator's algod.

The node listens on localhost and requires X-Algo-API-Token. The SPA (Pera
sign-in suggested params, later any other browser algod read) cannot reach
that socket, so we expose the safe public REST surface at /api/v1/algod/*
and inject the token server-side. Admin/dev algod routes stay off the list.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

import falcon
import httpx

from app.core.config import settings
from app.core.rate_limit import incr_with_expiry
from app.core.request_headers import client_ip

logger = logging.getLogger(__name__)

_RATE_KEY_PREFIX = "algorand:algod_proxy_rl:"
_RATE_WINDOW_SECONDS = 3600

_ADDR = r"[A-Z2-7]{58}"
_TXID = r"[A-Z2-7]{52}"
_ALLOWED_GET = re.compile(
    r"^(?:"
    r"genesis|health|"
    r"v2/status|"
    r"v2/status/wait-for-block-after/[0-9]+|"
    r"v2/transactions/params|"
    r"v2/transactions/pending|"
    r"v2/transactions/pending/" + _TXID + r"|"
    r"v2/ledger/supply|"
    r"v2/accounts/" + _ADDR + r"|"
    r"v2/assets/[0-9]+|"
    r"v2/applications/[0-9]+|"
    r"v2/blocks/[0-9]+"
    r")$"
)
_ALLOWED_POST = re.compile(r"^v2/transactions$")
_MAX_POST = 1_048_576


def allowed_algod_path(method: str, path: str) -> bool:
    """True when `path` (no leading slash) is a public algod route we will proxy."""
    cleaned = path.lstrip("/")
    if not cleaned or ".." in cleaned:
        return False
    verb = method.upper()
    if verb == "GET":
        return _ALLOWED_GET.match(cleaned) is not None
    if verb == "POST":
        return _ALLOWED_POST.match(cleaned) is not None
    return False


def _token_headers() -> dict[str, str]:
    token = settings.algod_token.strip()
    if token:
        return {"X-Algo-API-Token": token}
    return {}


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    return httpx.Client(timeout=8.0)


def forward_algod(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    query: str = "",
) -> tuple[int, bytes, str]:
    """Hit the configured algod. Returns (status, body, content-type)."""
    url = f"{settings.algod_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    response = _client().request(
        method.upper(),
        url,
        headers=_token_headers(),
        content=body or None,
    )
    ctype = response.headers.get("content-type", "application/json")
    return response.status_code, response.content, ctype


def _json_error(resp: falcon.Response, status: int, code: str, message: str) -> None:
    resp.status = falcon.code_to_http_status(status)
    resp.media = {"error": {"code": code, "message": message}}


def _rate_limited(headers: dict[str, str]) -> bool:
    """True when this IP has exceeded the hourly algod-proxy budget.

    Fails OPEN (a Redis hiccup reads as "not limited") -- same policy as the
    image proxy's rate limit: a Redis blip must not take down public algod
    reads. An unattributable request (no X-Real-IP / X-Forwarded-For) is not
    limited either, for the same reason -- one shared bucket would let
    unrelated callers starve each other.
    """
    ip = client_ip(headers)
    if not ip:
        return False
    count = incr_with_expiry(f"{_RATE_KEY_PREFIX}{ip}", window_seconds=_RATE_WINDOW_SECONDS)
    if count is None:
        return False
    return count > settings.algod_proxy_rate_limit_per_hour


class AlgodProxyResource:
    """Falcon resource: GET/POST /api/v1/algod/{rest:path}."""

    def on_get(self, req: falcon.Request, resp: falcon.Response, rest: str = "") -> None:
        self._handle(req, resp, rest, "GET")

    def on_post(self, req: falcon.Request, resp: falcon.Response, rest: str = "") -> None:
        self._handle(req, resp, rest, "POST")

    def _handle(
        self, req: falcon.Request, resp: falcon.Response, rest: str, method: str
    ) -> None:
        path = (rest or "").lstrip("/")
        if not allowed_algod_path(method, path):
            _json_error(resp, 404, "not_found", "unknown algod path")
            return
        if _rate_limited(dict(req.headers)):
            _json_error(
                resp, 429, "rate_limited", "Too many algod requests — please try again later"
            )
            return
        body = b""
        if method == "POST":
            body = req.bounded_stream.read(_MAX_POST + 1)
            if len(body) > _MAX_POST:
                _json_error(resp, 413, "too_large", "transaction too large")
                return
        query = req.query_string or ""
        try:
            status, payload, ctype = forward_algod(method, path, body=body, query=query)
        except Exception as exc:
            logger.warning("algod proxy %s /%s failed: %s", method, path, exc)
            _json_error(resp, 502, "algod", "algod unavailable")
            return
        resp.status = falcon.code_to_http_status(status)
        resp.content_type = ctype
        resp.data = payload


def register_algod_proxy(app: Any) -> None:
    """Attach the catch-all proxy onto a Falcon App (path converter, not :param)."""
    app.add_route("/api/v1/algod/{rest:path}", AlgodProxyResource())
