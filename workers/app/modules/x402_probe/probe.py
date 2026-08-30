"""One unpaid probe of one listed x402 endpoint: fetch, parse the 402 offer, never pay.

The probe sends a single request carrying no payment header, so the most an
endpoint can do is answer 402 with its offer. That offer is what we record:
whether it parsed (a base64 JSON `PAYMENT-REQUIRED` header with an
`accepts` list, or the same object as a JSON body) and the first
`accepts[].payTo`, which the sweep compares against the listing's payer to
grant or revoke the verified badge.

Network safety mirrors app.core.net_guard.guarded_get, which cannot be used
directly because it buffers the whole response: every hop (the URL and each
redirect target) goes through assert_public_url, redirects are followed by
hand, and the body is streamed and cut at max_body_bytes.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.core.config import (
    X402_PROBE_MAX_BODY_BYTES,
    X402_PROBE_TIMEOUT_SECONDS,
)
from app.core.http_client import get_http_client
from app.core.net_guard import UnsafeUrlError, assert_public_url

logger = logging.getLogger(__name__)

# Labelled so the probe's traffic is identifiable in any endpoint's logs and
# can never be mistaken for a customer (CLAUDE.md section 9: probe traffic is
# flagged and excluded from every ranking).
PROBE_USER_AGENT = (
    "PXke-x402-probe/1 (+https://algorand.pxke.me/x402; unpaid monitoring, never pays)"
)
_PAYMENT_REQUIRED_HEADER = "payment-required"
_MAX_REDIRECTS = 3


@dataclass(frozen=True)
class RawResponse:
    """What the transport hands back: enough to judge the offer, nothing more."""

    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one probe of one URL, as stored in x402_probe_results."""

    url: str
    probed_at: datetime
    reachable: bool
    http_status: int
    latency_ms: int
    served_valid_402: bool
    payto_seen: str
    error: str


Fetcher = Callable[[str], RawResponse]


def _stream_body(response: httpx.Response, cap: int) -> bytes:
    chunks: list[bytes] = []
    read = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        read += len(chunk)
        if read >= cap:
            break
    return b"".join(chunks)[:cap]


def fetch_unpaid(url: str) -> RawResponse:
    """GET `url` with no payment, SSRF-guarded on every hop, body capped.

    A 405 is retried once as a POST with an empty JSON object, since many
    x402 resources only answer POST. Raises UnsafeUrlError or an httpx
    error; the caller turns those into an unreachable ProbeResult.
    """
    client = get_http_client(timeout=X402_PROBE_TIMEOUT_SECONDS, follow_redirects=False)
    headers = {"User-Agent": PROBE_USER_AGENT, "Accept": "application/json"}
    current = url
    method = "GET"
    for _ in range(_MAX_REDIRECTS + 1):
        assert_public_url(current)
        with client.stream(
            method, current, headers=headers, content=b"{}" if method == "POST" else None
        ) as response:
            location = response.headers.get("location")
            if response.is_redirect and location:
                current = str(httpx.URL(str(response.url)).join(location))
                continue
            if response.status_code == 405 and method == "GET":
                method = "POST"
                continue
            body = _stream_body(response, X402_PROBE_MAX_BODY_BYTES)
            return RawResponse(
                status=response.status_code, headers=dict(response.headers), body=body
            )
    raise UnsafeUrlError("too many redirects")


def _decode_offer(raw: RawResponse) -> dict | None:
    """Return the 402 offer object from the header (preferred) or the body, or None."""
    header = next(
        (v for k, v in raw.headers.items() if k.lower() == _PAYMENT_REQUIRED_HEADER), None
    )
    if header:
        try:
            decoded = json.loads(base64.b64decode(header, validate=False))
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, dict):
            return decoded
    if raw.body:
        try:
            decoded = json.loads(raw.body)
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(decoded, dict) and "accepts" in decoded:
            return decoded
    return None


def parse_offer(raw: RawResponse) -> tuple[bool, str]:
    """Return (served_valid_402, first payTo) for a raw response.

    Valid means: HTTP 402, an offer object that parses, and a non-empty
    `accepts` list of objects. payTo is the first `payTo` (or `pay_to`)
    found in accepts, "" when none.
    """
    if raw.status != 402:
        return False, ""
    offer = _decode_offer(raw)
    if offer is None:
        return False, ""
    accepts = offer.get("accepts")
    if not isinstance(accepts, list) or not accepts:
        return False, ""
    if not all(isinstance(option, dict) for option in accepts):
        return False, ""
    for option in accepts:
        payto = option.get("payTo") or option.get("pay_to")
        if isinstance(payto, str) and payto.strip():
            return True, payto.strip()
    return True, ""


def probe_url(
    url: str, *, fetch: Fetcher = fetch_unpaid, now: datetime | None = None
) -> ProbeResult:
    """Probe one URL and return its ProbeResult; never raises for a per-URL failure."""
    probed_at = now or datetime.now(tz=UTC)
    started = time.monotonic()
    try:
        raw = fetch(url)
    except (UnsafeUrlError, httpx.HTTPError, OSError, ValueError) as exc:
        latency = int((time.monotonic() - started) * 1000)
        logger.info("x402 probe unreachable url=%s error=%s", url, exc)
        return ProbeResult(
            url=url,
            probed_at=probed_at,
            reachable=False,
            http_status=0,
            latency_ms=latency,
            served_valid_402=False,
            payto_seen="",
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
    latency = int((time.monotonic() - started) * 1000)
    valid, payto = parse_offer(raw)
    return ProbeResult(
        url=url,
        probed_at=probed_at,
        reachable=True,
        http_status=raw.status,
        latency_ms=latency,
        served_valid_402=valid,
        payto_seen=payto,
        error="",
    )
