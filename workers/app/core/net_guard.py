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

if TYPE_CHECKING:
    import httpx

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeUrlError(ValueError):
    """Raised when a URL is not a safe public target."""


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
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


def is_public_url(url: str) -> bool:
    """Return whether url resolves only to public (non-SSRF-able) addresses."""
    try:
        assert_public_url(url)
        return True
    except UnsafeUrlError:
        return False


def guarded_get(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: float = 12.0,
    max_redirects: int = 5,
) -> httpx.Response:
    """Httpx GET that re-validates the target on every redirect hop.

    follow_redirects must stay off here: otherwise a public URL could 302 to an
    internal one and the client would follow it before any guard runs. We follow
    manually and call assert_public_url before each request.
    """
    import httpx

    current = url
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            assert_public_url(current)
            response = client.get(current, headers=headers, params=params)
            location = response.headers.get("location")
            if response.is_redirect and location:
                current = str(httpx.URL(response.url).join(location))
                params = None  # query travels in the redirect target after hop 1
                continue
            return response
    raise UnsafeUrlError("too many redirects")
