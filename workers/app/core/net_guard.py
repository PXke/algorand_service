"""SSRF guard for outbound fetches of URLs we did not author.

The crawler and the investigative agent fetch URLs that come from discovered
links or from the LLM's tool calls. Without a guard, a planted link or a
hallucinated URL could make a worker hit internal services (Cassandra, Redis,
the admin API on localhost) or a cloud metadata endpoint (169.254.169.254).

assert_public_url resolves the host and rejects any URL whose host is, or
resolves to, a non-public IP. There is a small TOCTOU window between this check
and the actual connection (DNS rebinding); that is acceptable for our threat
model (preventing the agent/crawler from reaching internal ranges), and is the
standard pre-resolution approach.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from app.core.config import NET_GUARD_MAX_RESPONSE_BYTES

if TYPE_CHECKING:
    import httpx

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeUrlError(ValueError):
    """Raised when a URL is not a safe public target."""


class ResponseTooLargeError(Exception):
    """Raised when a fetched response exceeds the body-size cap.

    Deliberately NOT a UnsafeUrlError subclass: the URL itself may be a
    perfectly legitimate public host (see scrape_cooldown.py's own
    isinstance(_, UnsafeUrlError) check, which decides whether a URL is
    permanently unsafe to retry -- a too-large response is a different,
    response-shaped failure, not a host-shaped one).
    """


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # is_global additionally rejects the IANA special-purpose ranges the
    # flags above miss, notably 100.64.0.0/10 (CGNAT shared address space).
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or not addr.is_global
    )


def assert_public_url(url: str) -> str:
    """Return url unchanged if it targets a public host, else raise UnsafeUrlError."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"scheme not allowed: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("missing host")

    # Literal IP host: check it directly (no DNS).
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not _ip_is_public(host):
            raise UnsafeUrlError(f"non-public IP host: {host}")
        return url

    # Hostname: every resolved address must be public.
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"dns resolution failed for {host}") from exc
    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise UnsafeUrlError(f"no addresses for {host}")
    for a in addrs:
        if not _ip_is_public(a):
            raise UnsafeUrlError(f"host {host} resolves to non-public IP {a}")
    return url


def _read_bounded(response: httpx.Response, *, max_bytes: int) -> httpx.Response:
    """Read `response`'s body via streaming, aborting past `max_bytes`, and return an equivalent fully-buffered Response.

    Never trusts a declared Content-Length (spoofable, or simply absent on a
    chunked response) -- the cap is enforced against bytes actually read.
    Rebuilds a plain httpx.Response (status_code/headers/content, with
    `request` still bound so `.url` keeps working) rather than returning the
    streaming response itself, so every existing caller's `.text`/`.json()`/
    `.content`/`.url` access keeps working unchanged.
    """
    import httpx

    buf = bytearray()
    for chunk in response.iter_bytes():
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise ResponseTooLargeError(
                f"response body exceeds {max_bytes} byte cap (url={response.url})"
            )
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        content=bytes(buf),
        request=response.request,
    )


def guarded_request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: float = 12.0,
    max_redirects: int = 5,
    max_bytes: int | None = None,
) -> httpx.Response:
    """Httpx request that re-validates the target on every redirect hop and caps the response body.

    follow_redirects must stay off here: otherwise a public URL could 302 to an
    internal one and the client would follow it before any guard runs. We follow
    manually and call assert_public_url before each request.

    The final (non-redirect) response's body is streamed and capped at
    `max_bytes` (default settings.NET_GUARD_MAX_RESPONSE_BYTES) rather than
    buffered whole -- see that setting's own docstring (2026-09-07 security
    review, finding 6): a hostile or misconfigured target could otherwise be
    read into memory in full, one OOM'd worker per request. A redirect hop's
    body is never read at all, capped or not -- only its status/Location
    header matter.

    Uses the process-cached shared client (app.core.http_client.get_http_client)
    rather than opening a fresh httpx.Client per call: this is the fetch helper
    behind nearly every LLM-supplied and frontier-discovered URL in workers/,
    including link_extractor.enqueue_page_links's per-external-link domain
    preview (60-200 links per page is routine), so a fresh TCP/TLS handshake
    per call here was the single largest source of unreused connections in the
    codebase.
    """
    import httpx

    from app.core.http_client import get_http_client

    cap = NET_GUARD_MAX_RESPONSE_BYTES if max_bytes is None else max_bytes
    verb = method.upper()
    current = url
    client = get_http_client(timeout=timeout, follow_redirects=False)
    for _ in range(max_redirects + 1):
        assert_public_url(current)
        with client.stream(verb, current, headers=headers, params=params) as streamed:
            location = streamed.headers.get("location")
            if streamed.is_redirect and location:
                current = str(httpx.URL(streamed.url).join(location))
                params = None  # query travels in the redirect target after hop 1
                continue
            return _read_bounded(streamed, max_bytes=cap)
    raise UnsafeUrlError("too many redirects")


def guarded_get(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: float = 12.0,
    max_redirects: int = 5,
    max_bytes: int | None = None,
) -> httpx.Response:
    """SSRF-guarded, body-capped GET; see guarded_request for hop re-validation and the size cap."""
    return guarded_request(
        "GET",
        url,
        headers=headers,
        params=params,
        timeout=timeout,
        max_redirects=max_redirects,
        max_bytes=max_bytes,
    )
