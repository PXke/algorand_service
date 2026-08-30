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

Time safety: httpx's timeout is per read, so an endpoint trickling one byte
per few seconds (or bouncing through slow redirects) could hold one probe
for as long as it liked. fetch_unpaid therefore also enforces a wall-clock
deadline of DEADLINE_FACTOR x X402_PROBE_TIMEOUT_SECONDS over the whole
fetch -- every hop and every body chunk -- and gives up with
ProbeDeadlineExceeded, which probe_url records as reachable with
error="deadline" (the endpoint answered; it just never finished).

The recorded payTo is only ever a syntactically valid Algorand address
(algosdk's checksum check): payto_seen is served verbatim on free routes,
so anything else the offer carried is stored as "". served_valid_402 still
reflects whether the offer itself parsed.
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
from algosdk.encoding import is_valid_address

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
# Whole-fetch wall-clock budget, as a multiple of the per-read timeout.
DEADLINE_FACTOR = 2
# Seam for tests: the clock the deadline is measured on.
_monotonic = time.monotonic


class ProbeDeadlineExceeded(Exception):
    """The whole fetch (hops + body) outran DEADLINE_FACTOR x the probe timeout."""


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


def _check_deadline(deadline: float) -> None:
    if _monotonic() > deadline:
        raise ProbeDeadlineExceeded("probe deadline exceeded")


def _stream_body(response: httpx.Response, cap: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    read = 0
    for chunk in response.iter_bytes():
        _check_deadline(deadline)
        chunks.append(chunk)
        read += len(chunk)
        if read >= cap:
            break
    return b"".join(chunks)[:cap]


def fetch_unpaid(url: str) -> RawResponse:
    """GET `url` with no payment, SSRF-guarded on every hop, body capped, wall-clock bounded.

    A 405 is retried once as a POST with an empty JSON object, since many
    x402 resources only answer POST. Raises UnsafeUrlError, an httpx error
    or ProbeDeadlineExceeded (the whole fetch, redirects and body included,
    took longer than DEADLINE_FACTOR x X402_PROBE_TIMEOUT_SECONDS); the
    caller turns those into a ProbeResult.
    """
    client = get_http_client(timeout=X402_PROBE_TIMEOUT_SECONDS, follow_redirects=False)
    headers = {"User-Agent": PROBE_USER_AGENT, "Accept": "application/json"}
    deadline = _monotonic() + DEADLINE_FACTOR * X402_PROBE_TIMEOUT_SECONDS
    current = url
    method = "GET"
    for _ in range(_MAX_REDIRECTS + 1):
        _check_deadline(deadline)
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
            body = _stream_body(response, X402_PROBE_MAX_BODY_BYTES, deadline)
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
    found in accepts, "" when none -- or when the first one found is not a
    syntactically valid Algorand address (it is stored and served as-is on
    free routes, so nothing else is let through; the offer itself still
    counts as valid).
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
            candidate = payto.strip()
            if is_valid_address(candidate):
                return True, candidate
            logger.info("x402 probe ignoring non-Algorand payTo=%r", candidate[:80])
            return True, ""
    return True, ""


def probe_url(
    url: str, *, fetch: Fetcher = fetch_unpaid, now: datetime | None = None
) -> ProbeResult:
    """Probe one URL and return its ProbeResult; never raises for a per-URL failure."""
    probed_at = now or datetime.now(tz=UTC)
    started = time.monotonic()
    try:
        raw = fetch(url)
    except ProbeDeadlineExceeded:
        latency = int((time.monotonic() - started) * 1000)
        logger.info("x402 probe deadline exceeded url=%s latency_ms=%s", url, latency)
        return ProbeResult(
            url=url,
            probed_at=probed_at,
            reachable=True,
            http_status=0,
            latency_ms=latency,
            served_valid_402=False,
            payto_seen="",
            error="deadline",
        )
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
