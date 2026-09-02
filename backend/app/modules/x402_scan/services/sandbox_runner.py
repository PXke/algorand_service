"""Spins one ephemeral, hardened, network-isolated container per scan and parses its verdict.

Docker vs Firecracker (roadmap item 18b's required design decision, made
2026-09-01): this ships on Docker for v0. Reasoning:

  - The actual security boundary a per-file static scan needs is "no network
    egress, no host filesystem access beyond the one read-only input mount,
    hard resource caps, hard-killed on timeout, gone immediately after." All
    of that is available and battle-tested via Docker run flags (--network
    none, --read-only, --cap-drop=ALL, --security-opt=no-new-privileges,
    --pids-limit, --memory, non-root --user, --rm) -- see _run below.
  - This tool never executes the scanned file. Static-only analysis (read
    bytes, run ClamAV, run `file`, compute entropy, list archive members) has
    a far smaller kernel-attack-surface need than a sandbox meant to detonate
    and observe arbitrary executables. Firecracker's whole value proposition
    -- a real second kernel via a microVM, so a container-escape in the
    scanned payload can't reach the host kernel -- matters most for the
    execute-and-observe case this explicitly does NOT do.
  - Firecracker needs KVM, its own rootfs/kernel image pipeline, and a
    jailer/API-socket ops model this codebase has none of yet. That's real
    setup cost for a marginal security gain over a hardened, non-executing
    Docker container.
  - If this product later grows a dynamic/detonation mode (actually running
    the uploaded binary to observe behavior -- NOT scoped here, NOT built
    here), re-open this decision: that mode's risk profile is different
    enough that Firecracker's microVM isolation stops being optional.

Every container is single-use (`--rm`), gets its own tmpdir mount, and is
force-killed on a hard wall-clock timeout -- a scan that hangs the container
must not hang the paid HTTP request indefinitely.
"""

from __future__ import annotations

import json
import logging
import subprocess
import uuid

from app.modules.x402_scan.sandbox.checks import clamav

logger = logging.getLogger(__name__)

IMAGE = "algorand-x402-scan:v0"
_DOCKER_RUN_TIMEOUT_S = 90  # hard wall-clock cap; the process is killed past this
_DOCKER_KILL_TIMEOUT_S = 10


class SandboxError(Exception):
    """The container did not produce a parseable verdict."""


def run_scan(host_file_path: str, *, timeout_s: int = _DOCKER_RUN_TIMEOUT_S) -> dict:
    """Run the sandbox image against one file on disk; return its parsed JSON report.

    Raises SandboxError on any failure (container error, timeout, unparseable
    output) -- the caller decides how that maps to an HTTP response; this
    layer never guesses at a partial verdict on failure (CLAUDE.md invariant
    8: empty is not "none found").
    """
    # Unique per invocation so a timeout can `docker kill` this exact
    # container by name (security audit finding #2/#3: without --name, a
    # client-side subprocess timeout killed only the `docker run` CLI
    # process, leaving the 1.5GB container it started running indefinitely
    # -- a real resource-exhaustion vector on this shared host, not a
    # hypothetical one).
    container_name = f"x402-scan-{uuid.uuid4().hex}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--user",
        "10001:10001",  # explicit, not just relying on the image's USER
        # 384m, not 256m: security audit finding -- MAX_EXTRACT_BYTES
        # (checks/archive.py, 200MB) left only 56MB of tmpfs headroom for
        # extraction working overhead (scratch files, logs), effectively
        # making ENOSPC the real bomb-guard rather than the declared cap.
        "--tmpfs",
        "/tmp:size=384m",
        # 1536m, not the original 512m: a clamd sidecar agent's own
        # measurement found clamd's resident memory once fully loaded is
        # ~944MiB against the current signature DB -- 512m was silently
        # OOM-killing the clamscan fallback path outright (exit 137), not
        # just running it slowly. Applies to this per-request container too
        # since the clamscan fallback runs inside it, not the sidecar.
        "--memory",
        "1536m",
        "--memory-swap",
        "1536m",
        "--pids-limit",
        "128",
        "--cpus",
        "1",
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "-v",
        f"{host_file_path}:/scan/input:ro",
        # clamd fast-path socket, shared with the long-lived clamd sidecar
        # (see checks/clamav.py's module docstring and
        # sandbox/start_clamd_sidecar.sh) -- mounted READ-ONLY (security
        # audit finding: connecting to a Unix domain socket needs
        # read/write on the socket file itself, per LocalSocketMode in
        # clamd.conf, and search permission on the directory path, but NOT
        # write access to the directory -- :ro here means a compromised
        # per-request container (e.g. via a future ClamAV/yara/exiftool
        # parser CVE) cannot unlink or tamper with the shared
        # clamd.sock/pid/log and degrade the sidecar for every other
        # concurrent request.
        "-v",
        f"{clamav.CLAMD_SOCKET_DIR_HOST}:{clamav.CLAMD_SOCKET_DIR_CONTAINER}:ro",
        IMAGE,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run's own timeout already killed the docker-run CLIENT
        # process, but the container it started would keep running (a real
        # resource-exhaustion vector on this shared host -- security audit
        # finding #2/#3) unless explicitly killed by the --name set above.
        # Best-effort: a kill failure here is logged, not re-raised -- the
        # SandboxError below is the actual signal the caller acts on either way.
        try:
            subprocess.run(
                ["docker", "kill", container_name],
                capture_output=True,
                timeout=_DOCKER_KILL_TIMEOUT_S,
                check=False,
            )
        except Exception:
            logger.error(
                "x402 scan sandbox: failed to kill orphaned container %s after timeout",
                container_name,
                exc_info=True,
            )
        logger.error("x402 scan sandbox timed out after %ss", timeout_s)
        raise SandboxError(f"scan timed out after {timeout_s}s") from exc

    if proc.returncode not in (0, 1):
        # ClamAV's own exit code 1 ("infected found") propagates as the
        # container's exit code since it's the last thing scan.py's
        # subprocess call observes indirectly -- scan.py itself always exits
        # 0 and prints JSON regardless, so a non-0/1 code here means the
        # container itself failed (OOM-killed, image error), not a verdict.
        logger.error("x402 scan sandbox exited %s stderr=%s", proc.returncode, proc.stderr[-2000:])
        raise SandboxError(f"sandbox exited {proc.returncode}: {proc.stderr[-500:]}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        logger.error("x402 scan sandbox produced unparseable stdout: %s", proc.stdout[:2000])
        raise SandboxError("sandbox produced no parseable report") from exc
