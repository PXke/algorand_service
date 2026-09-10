"""One SSRF-guarded reachability check of one caller-supplied URL.

Uses `app.core.ssrf_guard.resolve_public_ip` for SSRF-safe DNS resolution
(resolve every address, reject on any private/loopback/link-local/reserved/
multicast/non-global hit, connect to the IP already validated -- never a
second, unvalidated lookup at connect time) -- the same primitive
`x402_scan/services/scan_service.py` and `media/api/routes.py` use for the
identical reason.

Unlike x402_scan/media, this never reads the response body -- a liveness
check needs only the status line and headers, not the content, so
`client.stream()`'s context is exited without ever calling `iter_bytes()`.
That is a real DDoS-amplification reduction, not just a speed win: a check
moves far less data against any one target than a full-body fetch would.

check_target() never raises for a target-side failure (DNS, refused,
timeout, TLS, non-public, too-many-redirects, or a plain non-2xx/3xx/4xx/5xx
transport hiccup) -- those are all valid answers for a check whose entire
point is sometimes saying "it's down." It only lets a genuine bug in this
function itself propagate.
"""

from __future__ import annotations

import logging
import socket
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

import httpx

from app.core.ssrf_guard import resolve_public_ip

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_UA = (
    "PXke-registry-liveness-check/1 (+https://algorand.pxke.me/registry; "
    "submit-time and periodic reachability check of a listed project page, not a crawl)"
)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True)
class UptimeResult:
    """One check's outcome -- always returned, never raised, for a target-side failure."""

    final_url: str
    reachable: bool
    http_status: int
    response_time_ms: int
    resolved_ip: str
    error: str
    redirect_chain: list[str] = field(default_factory=list)


def _classify_resolution_failure(host: str) -> str:
    """Distinguish "DNS never resolved" from "resolved, but only to a private/reserved address.".

    Pure diagnostic re-check for the response's `error` field -- makes no
    accept/reject decision of its own; `resolve_public_ip`'s verdict (already
    acted on by the caller) is the only thing that actually gated the
    connection. A second `getaddrinfo` call here is wasted DNS work, not a
    second copy of the SSRF policy.
    """
    try:
        socket.getaddrinfo(host, None)
    except OSError:
        return "dns_failure"
    return "non_public_target"


def check_target(
    url: str,
    *,
    timeout_s: float,
    max_redirects: int,
    transport: httpx.BaseTransport | None = None,
) -> UptimeResult:
    """Fetch `url` (SSRF-guarded, redirect-limited, body never read); return the outcome.

    `timeout_s` bounds each hop's connect+read-headers time individually
    (httpx's own connect/read timeout), so the worst-case wall-clock time for
    the whole call is bounded by `timeout_s * (max_redirects + 1)` -- no
    separate overall deadline is needed the way a body-streaming fetch
    (x402_scan) requires one, because there is no body to slow-drip here.

    `transport` is a test seam only (an `httpx.MockTransport`, same
    convention `media/api/routes.py._stream_fetch` already uses) -- never
    passed in production code, where it stays `None` and httpx opens a real
    connection to the SSRF-validated IP.
    """
    started = time.monotonic()
    redirect_chain: list[str] = []
    current = url
    last_ip = ""

    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=False, transport=transport) as client:
            for _ in range(max_redirects + 1):
                redirect_chain.append(current)
                parsed = urlparse(current)
                if parsed.scheme not in _ALLOWED_SCHEMES:
                    return _fail(started, "unsupported_scheme", current, redirect_chain, last_ip)
                host = parsed.hostname or ""
                ip = resolve_public_ip(host)
                if ip is None:
                    error = _classify_resolution_failure(host)
                    return _fail(started, error, current, redirect_chain, last_ip)
                last_ip = ip.strip("[]")  # resolve_public_ip brackets IPv6 literals
                port_suffix = f":{parsed.port}" if parsed.port else ""
                connect_url = urlunparse(parsed._replace(netloc=f"{ip}{port_suffix}"))

                try:
                    with client.stream(
                        "GET",
                        connect_url,
                        headers={"User-Agent": _UA, "Host": parsed.netloc},
                        extensions={"sni_hostname": host},
                    ) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                return _ok(
                                    started, response.status_code, current, redirect_chain, last_ip
                                )
                            current = str(httpx.URL(current).join(location))
                            continue
                        return _ok(started, response.status_code, current, redirect_chain, last_ip)
                except httpx.TimeoutException:
                    return _fail(started, "timeout", current, redirect_chain, last_ip)
                except httpx.ConnectError as exc:
                    cause = exc.__cause__
                    is_tls = isinstance(cause, ssl.SSLError) or "ssl" in str(exc).lower()
                    error = "tls_error" if is_tls else "connection_refused"
                    return _fail(started, error, current, redirect_chain, last_ip)
                except httpx.HTTPError:
                    return _fail(started, "http_error", current, redirect_chain, last_ip)

            return _fail(started, "too_many_redirects", current, redirect_chain, last_ip)
    except Exception:
        logger.exception("registry liveness check: unexpected failure checking url=%r", url)
        return _fail(started, "check_failed", current, redirect_chain, last_ip)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _ok(
    started: float, status: int, final_url: str, redirect_chain: list[str], resolved_ip: str
) -> UptimeResult:
    return UptimeResult(
        final_url=final_url,
        reachable=True,
        http_status=status,
        response_time_ms=_elapsed_ms(started),
        resolved_ip=resolved_ip,
        error="",
        redirect_chain=redirect_chain,
    )


def _fail(
    started: float, error: str, final_url: str, redirect_chain: list[str], resolved_ip: str
) -> UptimeResult:
    return UptimeResult(
        final_url=final_url,
        reachable=False,
        http_status=0,
        response_time_ms=_elapsed_ms(started),
        resolved_ip=resolved_ip,
        error=error,
        redirect_chain=redirect_chain,
    )


__all__ = ["UptimeResult", "check_target"]
