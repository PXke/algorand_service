"""media.api.routes._stream_fetch: streaming byte-cap abort and SSRF IP pinning.

No real DNS or network: `_resolve_public_ip` is monkeypatched (it's the seam
that would otherwise hit socket.getaddrinfo), and the actual HTTP transport
is httpx.MockTransport, which never opens a socket.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from app.modules.media.api import routes as media_routes


class _ChunkedStream(httpx.SyncByteStream):
    """Yields a large body in small chunks.

    So a consumer's abort-mid-stream logic actually sees multiple chunks,
    not one pre-sliced blob.
    """

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks


def _chunks(data: bytes, size: int = 4096) -> Iterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


def test_stream_fetch_aborts_past_max_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """An upstream body larger than _MAX_BYTES is truncated, not fully buffered."""
    monkeypatch.setattr(media_routes, "_resolve_public_ip", lambda _host: "203.0.113.5")
    oversized = b"a" * (media_routes._MAX_BYTES + 10_000)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "image/png"}, stream=_ChunkedStream(_chunks(oversized))
        )

    status, ctype, data = media_routes._stream_fetch(
        "https://example.com/big.png", transport=httpx.MockTransport(handler)
    )
    assert status == 200
    assert ctype == "image/png"
    assert len(data) == media_routes._MAX_BYTES


def test_stream_fetch_pins_connection_to_resolved_ip_and_preserves_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connects to the pre-validated IP, not a fresh DNS lookup, but keeps the real Host/SNI.

    Connects to the IP `_resolve_public_ip` validated (never a second,
    unvalidated DNS lookup at connect time), while still sending the
    original Host header and TLS SNI so virtual-hosted upstreams keep
    working.
    """
    monkeypatch.setattr(media_routes, "_resolve_public_ip", lambda _host: "203.0.113.9")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url_host"] = request.url.host
        seen["host_header"] = request.headers.get("host")
        seen["sni_hostname"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG")

    status, ctype, data = media_routes._stream_fetch(
        "https://cdn.example.com/hero.png", transport=httpx.MockTransport(handler)
    )
    assert status == 200
    assert ctype == "image/png"
    assert data == b"\x89PNG"
    assert seen["url_host"] == "203.0.113.9"  # connected to the resolved IP, not the hostname
    assert seen["host_header"] == "cdn.example.com"
    assert seen["sni_hostname"] == "cdn.example.com"


def test_stream_fetch_rejects_when_host_does_not_resolve_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A private/unresolvable host (SSRF guard) never reaches the transport at all."""
    monkeypatch.setattr(media_routes, "_resolve_public_ip", lambda _host: None)

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("transport should never be reached for a blocked host")

    status, ctype, data = media_routes._stream_fetch(
        "https://internal.example/secret.png", transport=httpx.MockTransport(handler)
    )
    assert status == 502
    assert ctype == ""
    assert data == b""


def test_resolve_public_ip_rejects_private_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host that resolves to a private/loopback address is rejected."""

    def fake_getaddrinfo(_host: str, _port: int | None) -> list[tuple]:
        return [(None, None, None, None, ("127.0.0.1", 0))]

    monkeypatch.setattr(media_routes.socket, "getaddrinfo", fake_getaddrinfo)
    assert media_routes._resolve_public_ip("localhost.attacker.example") is None


def test_resolve_public_ip_accepts_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host that resolves to a single public address returns that IP."""

    def fake_getaddrinfo(_host: str, _port: int | None) -> list[tuple]:
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr(media_routes.socket, "getaddrinfo", fake_getaddrinfo)
    assert media_routes._resolve_public_ip("example.com") == "93.184.216.34"


def test_resolve_public_ip_rejects_when_any_resolved_address_is_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host round-robining between a public and an internal IP must not pass."""

    def fake_getaddrinfo(_host: str, _port: int | None) -> list[tuple]:
        return [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("10.0.0.5", 0)),
        ]

    monkeypatch.setattr(media_routes.socket, "getaddrinfo", fake_getaddrinfo)
    assert media_routes._resolve_public_ip("mixed.example") is None
