from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import socket
from urllib.parse import unquote, urljoin, urlparse

import httpx
from robyn import Request, Response

# Same-origin image proxy. Flutter web (CanvasKit) renders Image.network by
# FETCHING the image via XHR, which needs CORS headers most external hosts
# (dappradar, S3 buckets, og-image CDNs) don't send — so external article hero
# images silently fail. We re-serve them from our own origin (global CORS header
# applies), fixing rendering. SSRF-guarded: every hop must resolve to a public IP.

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_BYTES = 5_000_000
_TIMEOUT = 8.0
_MAX_REDIRECTS = 3
_CACHE_TTL = 86_400  # 24h: serve the same image from Redis instead of re-fetching
_UA = "algorand-platform-image-proxy/1.0 (+https://algorand.pxke.me)"


def _cache_key(url: str) -> str:
    return f"imgproxy:{hashlib.sha256(url.encode()).hexdigest()}"


def _redis():
    import redis

    from app.core.config import settings

    return redis.from_url(settings.redis_url, decode_responses=False)


def _cache_get(url: str) -> tuple[str, bytes] | None:
    """Return (content_type, data) from Redis, or None. Packed as ctype\\0data."""
    try:
        raw = _redis().get(_cache_key(url))
    except Exception:
        return None
    if not raw:
        return None
    sep = raw.find(b"\0")
    if sep < 0:
        return None
    return raw[:sep].decode("latin-1"), raw[sep + 1 :]


def _cache_set(url: str, ctype: str, data: bytes) -> None:
    with contextlib.suppress(Exception):
        _redis().set(_cache_key(url), ctype.encode("latin-1") + b"\0" + data, ex=_CACHE_TTL)


def _is_public_host(host: str) -> bool:
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return False
    return True


def _fetch_image(url: str) -> httpx.Response | None:
    """Fetch an image, re-validating the host on each redirect hop (SSRF safe)."""
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(url)
            if parsed.scheme not in _ALLOWED_SCHEMES or not _is_public_host(parsed.hostname or ""):
                return None
            try:
                resp = client.get(url, headers={"User-Agent": _UA})
            except Exception:
                return None
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if not loc:
                    return None
                url = urljoin(url, loc)
                continue
            return resp
    return None


def register_media_routes(app) -> None:
    @app.get("/api/v1/img")
    async def proxy_image(request: Request) -> Response:
        # robyn does NOT URL-decode query params, so the percent-encoded url
        # arrives literally — decode it before parsing.
        url = unquote((request.query_params.get("url", "") or "").strip())
        if not url:
            return Response(status_code=400, headers={}, description="missing url")
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
            return Response(status_code=400, headers={}, description="bad url")

        cached = _cache_get(url)
        if cached is not None:
            ctype, data = cached
            return Response(
                status_code=200,
                headers={
                    "Content-Type": ctype,
                    "Cache-Control": "public, max-age=86400",
                    "X-Cache": "HIT",
                },
                description=data,
            )

        resp = _fetch_image(url)
        if resp is None:
            return Response(status_code=502, headers={}, description="fetch failed")
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if resp.status_code != 200 or not ctype.startswith("image/"):
            return Response(status_code=404, headers={}, description="not an image")

        data = resp.content[:_MAX_BYTES]
        _cache_set(url, ctype, data)
        return Response(
            status_code=200,
            headers={
                "Content-Type": ctype,
                "Cache-Control": "public, max-age=86400",
                "X-Cache": "MISS",
            },
            description=data,
        )
