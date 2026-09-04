"""media.api.routes._stream_fetch: streaming byte-cap abort and SSRF IP pinning.

No real DNS or network: `resolve_public_ip` is monkeypatched (it's the seam
that would otherwise hit socket.getaddrinfo), and the actual HTTP transport
is httpx.MockTransport, which never opens a socket.

Dedicated `resolve_public_ip` resolution-policy tests (private/public/mixed/
CGNAT addresses) live in tests/test_ssrf_guard.py, next to the function's new
home in app.core.ssrf_guard -- not duplicated here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Never

import httpx
import pytest

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request
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
    monkeypatch.setattr(media_routes, "resolve_public_ip", lambda _host: "203.0.113.5")
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

    Connects to the IP `resolve_public_ip` validated (never a second,
    unvalidated DNS lookup at connect time), while still sending the
    original Host header and TLS SNI so virtual-hosted upstreams keep
    working.
    """
    monkeypatch.setattr(media_routes, "resolve_public_ip", lambda _host: "203.0.113.9")
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
    monkeypatch.setattr(media_routes, "resolve_public_ip", lambda _host: None)

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("transport should never be reached for a blocked host")

    status, ctype, data = media_routes._stream_fetch(
        "https://internal.example/secret.png", transport=httpx.MockTransport(handler)
    )
    assert status == 502
    assert ctype == ""
    assert data == b""


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        _ = key, seconds
        return True


class _BrokenRedis:
    def incr(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def expire(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")


def test_image_proxy_is_rate_limited_per_ip_and_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Third call from one IP within a budget of 2 is limited; another IP is not; Redis failure fails open."""
    monkeypatch.setattr(settings, "img_proxy_rate_limit_per_hour", 2)
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda: fake)

    def req(ip: str) -> Request:
        return Request(
            method="GET",
            headers={"X-Real-IP": ip},
            query_params=QueryParams({}),
            path_params={},
        )

    assert media_routes._image_proxy_rate_limited(req("203.0.113.9")) is False
    assert media_routes._image_proxy_rate_limited(req("203.0.113.9")) is False
    assert media_routes._image_proxy_rate_limited(req("203.0.113.9")) is True
    assert media_routes._image_proxy_rate_limited(req("203.0.113.10")) is False

    monkeypatch.setattr(rate_limit_core, "get_redis", _BrokenRedis)
    assert media_routes._image_proxy_rate_limited(req("203.0.113.9")) is False
