"""SSRF-safe DNS resolution primitive, shared by every outbound-fetch module.

`resolve_public_ip` is the one home of the SSRF policy (which ranges are
blocked, IPv6 handling, "reject on ANY non-public address in the result
set"); the media image proxy, the ecosystem liveness checker and the
`x402_scan` fetch all call it rather than re-deriving it (CLAUDE.md forbids
a new copy of existing logic).
"""

from __future__ import annotations

import ipaddress
import socket


def resolve_public_ip(host: str) -> str | None:
    """Resolve `host` and return one public IP literal to connect to.

    Returns None if `host` is empty, unresolvable, or ANY resolved address
    is private/internal.

    Rejecting on any non-public address in the result set (not just the first)
    closes a DNS trick where a host round-robins between a public IP and an
    internal one. The caller connects to the IP this function returns instead
    of letting httpx re-resolve the hostname at connect time -- otherwise a
    DNS answer that changes between this check and the actual TCP connect
    (DNS rebinding) would bypass the SSRF guard entirely.
    """
    if not host:
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return None
    ips: list[str] = []
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
            or not addr.is_global
        ):
            return None
        ips.append(f"[{addr}]" if addr.version == 6 else str(addr))
    return ips[0] if ips else None


__all__ = ["resolve_public_ip"]
