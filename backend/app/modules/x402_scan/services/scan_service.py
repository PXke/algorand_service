"""Fetch-bound, isolate-on-disk, sandbox-scan, always-cleanup orchestration for one file.

Uses `app.core.ssrf_guard.resolve_public_ip` for the SSRF-safe DNS
resolution (rebinding-proof: connects to the IP already validated, never a
second unvalidated lookup at connect time) rather than re-deriving that
logic — CLAUDE.md forbids a new copy of existing logic. It does NOT reuse
`media.api.routes._stream_fetch` itself: that helper is media-proxy-specific
(hard-gates on `content-type: image/*`, which is the wrong gate for an
arbitrary file/tarball scan target). `resolve_public_ip` originated in
`media.api.routes` as a private helper; it has since been promoted to the
shared `app.core.ssrf_guard` module now that a third real consumer
(x402_uptime, after this one) exists.

The file never touches the shared prod filesystem outside its own
tempdir-per-request, and that tempdir is always removed (`finally`), pass or
fail, empty verdict or infected -- CLAUDE.md invariant: a failure path must
not leave a scanned artifact sitting on disk.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx

from app.core.config import settings
from app.core.ssrf_guard import resolve_public_ip
from app.modules.x402_scan.services.sandbox_runner import SandboxError, run_scan

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_REDIRECTS = 3
_UA = "algorand-platform-x402-scan/0.1-prototype (+https://algorand.pxke.me)"


class FetchError(Exception):
    """The target URL could not be safely and boundedly fetched."""


def _fetch_bounded_to_disk(url: str, dest: Path, *, max_bytes: int, timeout_s: float) -> int:
    """Stream `url` straight to `dest` on disk, SSRF-pinned per hop, aborting past `max_bytes`.

    Streams to disk rather than buffering in memory: at the original 50MB
    cap an in-memory bytearray was fine, but raising the cap toward the
    "up to a few GB via URL" ask means an in-memory buffer would be a real
    per-request memory spike on a shared backend process under concurrent
    paid traffic. Disk, not memory, is the resource this now spends.
    Returns the number of bytes actually written.
    """
    with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(url)
            host = parsed.hostname or ""
            if parsed.scheme not in _ALLOWED_SCHEMES:
                raise FetchError(f"unsupported scheme: {parsed.scheme!r}")
            ip = resolve_public_ip(host)
            if ip is None:
                raise FetchError("target host does not resolve to a public address")
            port_suffix = f":{parsed.port}" if parsed.port else ""
            connect_url = urlunparse(parsed._replace(netloc=f"{ip}{port_suffix}"))
            with client.stream(
                "GET",
                connect_url,
                headers={"User-Agent": _UA, "Host": parsed.netloc},
                extensions={"sni_hostname": host},
            ) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        raise FetchError("redirect with no Location header")
                    url = httpx.URL(url).join(loc).human_repr()
                    continue
                if resp.status_code != 200:
                    raise FetchError(f"upstream returned HTTP {resp.status_code}")
                written = 0
                with dest.open("wb") as fh:
                    for chunk in resp.iter_bytes():
                        written += len(chunk)
                        if written > max_bytes:
                            raise FetchError(f"target exceeds {max_bytes} byte cap")
                        fh.write(chunk)
                return written
    raise FetchError("too many redirects")


def scan_url(url: str) -> dict:
    """Fetch `url` (bounded, SSRF-safe, streamed straight to disk), scan it, return the report.

    Raises FetchError or SandboxError; the route maps those to HTTP errors.
    The downloaded file lives only in one request-scoped tempdir, always
    removed on the way out regardless of outcome.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="x402scan_"))
    target_path = tmpdir / "input"
    try:
        download_bytes = _fetch_bounded_to_disk(
            url,
            target_path,
            max_bytes=settings.x402_scan_max_download_bytes,
            timeout_s=settings.x402_scan_download_timeout_s,
        )
        # 0644: the sandbox container runs as a different uid (10001) and
        # reads this bind-mount read-only -- it must be world-readable to be
        # readable at all across that uid boundary.
        target_path.chmod(0o644)
        tmpdir.chmod(0o755)

        report = run_scan(str(target_path), timeout_s=settings.x402_scan_sandbox_timeout_s)
        report["source_url"] = url
        report["download_bytes"] = download_bytes
        return report
    finally:
        try:
            if target_path.exists():
                target_path.unlink()
            tmpdir.rmdir()
        except OSError:
            logger.warning("x402 scan: failed to clean up tempdir %s", tmpdir, exc_info=True)


__all__ = ["FetchError", "SandboxError", "scan_url"]
