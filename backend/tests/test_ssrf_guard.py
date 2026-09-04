"""app.core.ssrf_guard.resolve_public_ip: the shared SSRF-safe DNS resolution primitive.

Moved here from tests/test_media_routes.py when `resolve_public_ip` was
promoted out of `media.api.routes` (originally `_resolve_public_ip`) into
this shared module -- see app/core/ssrf_guard.py's own docstring. No real
DNS: `socket.getaddrinfo` is monkeypatched.
"""

from __future__ import annotations

import pytest

from app.core import ssrf_guard


def test_resolve_public_ip_rejects_private_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host that resolves to a private/loopback address is rejected."""

    def fake_getaddrinfo(_host: str, _port: int | None) -> list[tuple]:
        return [(None, None, None, None, ("127.0.0.1", 0))]

    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", fake_getaddrinfo)
    assert ssrf_guard.resolve_public_ip("localhost.attacker.example") is None


def test_resolve_public_ip_accepts_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host that resolves to a single public address returns that IP."""

    def fake_getaddrinfo(_host: str, _port: int | None) -> list[tuple]:
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", fake_getaddrinfo)
    assert ssrf_guard.resolve_public_ip("example.com") == "93.184.216.34"


def test_resolve_public_ip_rejects_when_any_resolved_address_is_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host round-robining between a public and an internal IP must not pass."""

    def fake_getaddrinfo(_host: str, _port: int | None) -> list[tuple]:
        return [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("10.0.0.5", 0)),
        ]

    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", fake_getaddrinfo)
    assert ssrf_guard.resolve_public_ip("mixed.example") is None


def test_resolve_public_ip_rejects_cgnat(monkeypatch: pytest.MonkeyPatch) -> None:
    """100.64.0.0/10 is not is_private; is_global must still reject it."""

    def fake_getaddrinfo(_host: str, _port: int | None) -> list[tuple]:
        return [(None, None, None, None, ("100.64.0.1", 0))]

    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", fake_getaddrinfo)
    assert ssrf_guard.resolve_public_ip("cgnat.attacker.example") is None
