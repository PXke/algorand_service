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
_TIMEOUT = 3.0
_MAX_REDIRECTS = 3
_CACHE_TTL = 86_400  # 24h: serve the same image from Redis instead of re-fetching
_UA = "algorand-platform-image-proxy/1.0 (+https://algorand.pxke.me)"
# Optimisation caps: article heroes/cards never render wider than ~1200 CSS px,
# but sources ship multi-MB OG PNGs (one page weighed 10MB of images). Resize
# down + re-encode WebP before caching; pass through SVG/GIF and tiny files.
_MAX_DIM = 1200
_WEBP_QUALITY = 80
_OPTIMIZE_MIN_BYTES = 30_000
# 1×1 transparent WebP returned when upstream has no image (favicon misses,
# broken OG URLs). Keeps Lighthouse console clean and lets Image.network paint
# without a failed XHR.
_PLACEHOLDER_WEBP = (
    b"RIFF@\x00\x00\x00WEBPVP8X\n\x00\x00\x00\x10\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00ALPH\x02\x00\x00\x00\x00\x00VP8 \x18\x00\x00\x000\x01\x00\x9d"
    b"\x01*\x01\x00\x01\x00\x01@&%\xa4\x00\x03p\x00\xfe\xfd6h\x00"
)


def _cache_key(url: str) -> str:
    # v2: cache holds the OPTIMIZED copy — new prefix so pre-optimisation fat
    # entries age out instead of being served for another TTL.
    return f"imgproxy2:{hashlib.sha256(url.encode()).hexdigest()}"


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


def _optimize(ctype: str, data: bytes) -> tuple[str, bytes]:
    """Downscale to <=_MAX_DIM px and re-encode as WebP. Pass through SVGs
    (not raster), GIFs (animation), tiny files, and anything Pillow can't
    read; keep the original whenever it is already smaller. Never upscales."""
    if ctype in ("image/svg+xml", "image/gif") or len(data) < _OPTIMIZE_MIN_BYTES:
        return ctype, data
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(data))
        img.load()
        if max(img.size) > _MAX_DIM:
            img.thumbnail((_MAX_DIM, _MAX_DIM), Image.LANCZOS)
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if has_alpha else "RGB")
        out = BytesIO()
        img.save(out, format="WEBP", quality=_WEBP_QUALITY, method=4)
        optimized = out.getvalue()
        if len(optimized) < len(data):
            return "image/webp", optimized
        return ctype, data
    except Exception:
        return ctype, data


def _fetch_and_optimize(url: str) -> tuple[int, str, bytes]:
    """(status, content_type, body) — the blocking miss path, run off-loop."""
    resp = _fetch_image(url)
    if resp is None:
        return 502, "", b""
    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if resp.status_code != 200 or not ctype.startswith("image/"):
        return 404, "", b""
    ctype, data = _optimize(ctype, resp.content[:_MAX_BYTES])
    _cache_set(url, ctype, data)
    return 200, ctype, data


def register_media_routes(app) -> None:
    @app.get("/api/v1/img")
    async def proxy_image(request: Request) -> Response:
        import asyncio

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

        # Fetch + Pillow re-encode are blocking — keep them off the event loop.
        status, ctype, data = await asyncio.to_thread(_fetch_and_optimize, url)
        if status == 502:
            return Response(
                status_code=200,
                headers={
                    "Content-Type": "image/webp",
                    "Cache-Control": "public, max-age=3600",
                    "X-Cache": "PLACEHOLDER",
                },
                description=_PLACEHOLDER_WEBP,
            )
        if status == 404:
            return Response(
                status_code=200,
                headers={
                    "Content-Type": "image/webp",
                    "Cache-Control": "public, max-age=3600",
                    "X-Cache": "PLACEHOLDER",
                },
                description=_PLACEHOLDER_WEBP,
            )
        return Response(
            status_code=200,
            headers={
                "Content-Type": ctype,
                "Cache-Control": "public, max-age=86400",
                "X-Cache": "MISS",
            },
            description=data,
        )
