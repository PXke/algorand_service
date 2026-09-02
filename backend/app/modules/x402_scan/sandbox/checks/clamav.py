"""ClamAV signature scan: the one check here with a real "yes/no known bad file" verdict.

Fast path: talk to a long-lived `clamd` daemon (see ../clamd.conf and
../start_clamd_sidecar.sh) over a Unix domain socket shared with this
container via a bind-mounted host directory. The daemon loads ClamAV's
signature DB into memory once and keeps it warm, so a scan costs tens of
milliseconds instead of `clamscan`'s own full-DB-reload-from-disk on every
invocation -- measured at ~12.4s of pure reload time before a single byte is
scanned, dominating this endpoint's end-to-end latency (12-17s observed).
Deliberately never a network socket: the per-request container keeps
`--network none` (see ../../services/sandbox_runner.py); a Unix socket file
shared through a bind mount needs no network namespace at all.

Fallback path: if the daemon socket isn't present (sidecar not started, or
not mounted into this container) or clamdscan fails to talk to it for ANY
reason, fall back to standalone `clamscan` -- today's pre-existing behavior,
correctness-preserving, just slower. This must never be a hard failure and
must never silently report "clean" just because the fast path was
unavailable -- a missing/not-yet-started daemon always degrades to the slow
but correct path.

Two paths matter here and must not be confused:

  - CLAMD_SOCKET_DIR_CONTAINER ("/run/clamav"): the path INSIDE every
    container (both the clamd sidecar and every per-request scan container)
    where the shared socket directory is mounted. This must match
    ../clamd.conf's `LocalSocket` directive exactly -- it is a fixed,
    baked-in path, not configurable per-container.
  - CLAMD_SOCKET_DIR_HOST ("/var/run/x402-clamd"): the HOST-side directory
    both ../start_clamd_sidecar.sh and services/sandbox_runner.py bind-mount
    FROM. This one only needs the two sides to agree with each other; it is
    not baked into the image.

This is the one fixed contract with ../../services/sandbox_runner.py's
`docker run` invocation, which this module does NOT edit (see the
accompanying report): that file's cmd list must bind-mount
CLAMD_SOCKET_DIR_HOST read-write into the per-request container at
CLAMD_SOCKET_DIR_CONTAINER, in addition to its existing
`-v <file>:/scan/input:ro` mount, e.g.:

    "-v", f"{CLAMD_SOCKET_DIR_HOST}:{CLAMD_SOCKET_DIR_CONTAINER}",

(placed anywhere in the flag list before the trailing IMAGE argument).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# See the module docstring above for why there are two of these.
CLAMD_SOCKET_DIR_HOST = "/var/run/x402-clamd"
CLAMD_SOCKET_DIR_CONTAINER = "/run/clamav"
CLAMD_SOCKET_PATH = f"{CLAMD_SOCKET_DIR_CONTAINER}/clamd.sock"
# Same conf the sidecar itself is started with (../clamd.conf, baked into
# the image at build time -- see ../Dockerfile) so the client and the daemon
# always agree on the socket path without duplicating it in two places.
CLAMD_CLIENT_CONFIG = "/etc/clamav/clamd-sidecar.conf"

_CLAMDSCAN_TIMEOUT_S = 30
# 75s, not the container's full 90s wall clock (sandbox_runner.py's
# _DOCKER_RUN_TIMEOUT_S): this subprocess timeout must fire BEFORE the
# outer container timeout, or it can never actually trigger -- the whole
# container gets killed first, and this number becomes dead/misleading.
# Leaves margin for the other checks running concurrently in the same
# container (follow-up audit finding).
_CLAMSCAN_TIMEOUT_S = 75


def _socket_present() -> bool:
    """Whether the shared clamd socket exists in this container right now.

    A thin wrapper (rather than calling Path().exists() inline) so tests can
    monkeypatch just this seam without touching the real filesystem.
    """
    return Path(CLAMD_SOCKET_PATH).exists()


def _try_clamdscan(path: str) -> subprocess.CompletedProcess[str] | None:
    """Attempt the warm-daemon path. Returns None (never raises) if it isn't usable.

    None covers every "fast path unavailable" case uniformly: the socket
    file is missing, clamdscan can't connect, the daemon errors out, or the
    subprocess itself blows up -- the caller always treats None the same
    way, by falling back to clamscan.
    """
    if not _socket_present():
        return None
    try:
        proc = subprocess.run(
            [
                "clamdscan",
                "--config-file",
                CLAMD_CLIENT_CONFIG,
                "--stream",
                "--no-summary",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=_CLAMDSCAN_TIMEOUT_S,
        )
    except Exception:
        return None
    if proc.returncode not in (0, 1):
        # clamdscan exit code 2 covers both "can't reach clamd" (stale
        # socket file, daemon crashed/still warming up) and other
        # daemon-side errors -- never trust it as a scan verdict, always
        # treat it the same as "fast path unavailable" and fall back.
        return None
    return proc


def run(path: str) -> dict:
    """Scan `path` (a file or a directory, for the archive-extraction case) for known malware.

    Tries the warm clamd daemon first; falls back to standalone clamscan
    (slower, but always correct) if the daemon isn't reachable for any
    reason. Never raises.
    """
    engine = "clamdscan"
    proc = _try_clamdscan(path)
    if proc is None:
        engine = "clamscan"
        try:
            proc = subprocess.run(
                [
                    "clamscan",
                    "--no-summary",
                    "-r",
                    # Explicit, not relying on libclamav's compiled-in
                    # defaults -- matches clamd.conf's own limits (see that
                    # file's comment for why this matters: a silently
                    # smaller cap than x402_scan_max_download_bytes means a
                    # skipped-for-size file reads as "clean" by default).
                    "--max-filesize=1024M",
                    "--max-scansize=1024M",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=_CLAMSCAN_TIMEOUT_S,
            )
        except Exception as exc:
            return {"error": str(exc), "risk_score": 0.0}

    if proc.returncode not in (0, 1):
        # Neither "clean" (0) nor "infected" (1) -- the scanner itself
        # failed (e.g. OOM-killed mid-scan: returncode -9). This is a real
        # error, not a verdict -- surface it via "error" (visible to a human
        # reviewing the report) and deliberately do NOT set "clean": True or
        # leave infected_files/clean fields that would read as a completed,
        # clean scan (CLAUDE.md invariant: empty is not "none found").
        logger.warning(
            "%s exited %s (not clean, not infected): %s",
            engine,
            proc.returncode,
            proc.stderr[-500:],
        )
        return {"engine": engine, "error": f"{engine} exited {proc.returncode}", "risk_score": 0.0}

    # Both clamdscan and clamscan exit 0 clean / 1 infected, and both print
    # a trailing "FOUND" per matched line in --no-summary mode.
    infected = [line for line in proc.stdout.splitlines() if line.endswith("FOUND")]

    # Defense in depth against the same trap the size-limit config fix
    # (clamd.conf / --max-filesize above) already closes: if ClamAV ever
    # skips a target for exceeding ITS OWN size limit (independent of
    # whatever cap this codebase configures -- a future config drift, an
    # engine version default, an archive member that slips past this
    # sandbox's own caps), that line ends in neither "FOUND" nor a normal
    # "OK" and must never be read as a completed clean scan.
    skipped = [
        line
        for line in proc.stdout.splitlines()
        if "size limit" in line.lower() or line.rstrip().endswith("ERROR")
    ]
    if skipped:
        logger.warning("%s reported a scan skip, not a verdict: %s", engine, skipped)
        return {
            "engine": engine,
            "error": "target was not fully scanned (size-limit or engine skip)",
            "raw": skipped,
            "risk_score": 0.0,
        }

    clean = proc.returncode == 0
    result = {
        "engine": engine,
        "exit_code": proc.returncode,
        "infected_files": infected,
        "clean": clean,
    }
    if not clean and infected:
        result["risk_score"] = 1.0
        result["caution_notes"] = [f"ClamAV signature match: {line}" for line in infected]
    return result
