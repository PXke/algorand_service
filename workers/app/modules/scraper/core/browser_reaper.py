"""Periodic sweep that kills orphaned Playwright/Chromium OS processes.

Root cause (found live 2026-08-26 investigating a 29-process/~1.7GB leak,
load average 8-10): every browser call site in this codebase (fetch_page,
click_and_read, PlaywrightSession -- see browser_scrape.py) already cleans up
correctly via try/finally on every NORMAL exit path, including
SoftTimeLimitExceeded (it's a plain Exception subclass, so it's caught by the
broad `except Exception:` around llm_compose.py's tool loop, which still runs
that try's `finally: playwright_session.close()` before falling back to a
single-shot compose). None of that helps when the celery worker PROCESS
itself is killed out from under the browser instead of the task raising an
exception inside it:

  - A hard `time_limit` fires: billiard's TimeoutHandler kills the worker
    process directly (SIGKILL) with zero chance for Python to run anything.
  - Every deploy restarts algorand-platform-celery.service with
    `KillSignal=SIGQUIT` (see its unit file and deploy.sh's own comment above
    the restart step: "a celery restart sends SIGQUIT, which silently kills
    an in-flight compose (no exception handler runs)") -- billiard's cold
    shutdown path (`Pool.terminate` -> `terminate_job`) kills the worker
    process the same forceful way.
  - `celery control revoke <id> terminate=True` (used by admin/ops tooling to
    unstick a hung task) sends SIGTERM directly to the worker process, same
    story.

Confirmed live on the prod box (2026-08-26) that this is a single-PID kill,
not a process-group kill: `ps -o pid,ppid,pgid,sid` on a running worker child
and its Playwright driver/node child both showed the SAME pgid as the
top-level celery worker process (not their own pid) -- billiard's
group-vs-single-kill branch in pool.py only killpg()s a worker that is its
OWN process group leader (`os.getpgid(pid) == pid`), which is not how this
prefork pool is configured. So a forceful kill only ever signals the single
worker pid; the Playwright driver Node process (and therefore its Chromium
child, which `ps` shows as its own SESSION leader -- Chromium calls setsid()
on launch, so it isn't even in the worker's process group in the first
place) is simply never signaled at all and is orphaned, reparented to init.

Nothing in application code can fix this -- there is no Python to run once
the OS has already delivered SIGKILL, and no `atexit`/signal handler in OUR
process helps a process that was never our process's child.  A periodic
reaper is the standard mitigation for exactly this class of problem: find
Playwright/Chromium process trees whose lineage no longer traces back to a
LIVE celery worker process, and kill them.

Deliberately conservative:
  - only ever touches `chrome-headless-shell` / `playwright/driver/node`
    processes (never anything else on the box);
  - requires BOTH "no live celery worker ancestor" AND "older than
    min_age_seconds" before killing anything, so a process that's mid-launch
    (its parent linkage hasn't stabilized yet, or it just hasn't been
    reparented yet) is never mistaken for an orphan -- it just gets caught on
    a later sweep if it's still around and still orphaned then.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DRIVER_MARKER = "playwright/driver/node"
_BROWSER_MARKER = "chrome-headless-shell"
# Both substrings must appear (case-insensitively) in a process's full command
# line for it to count as a live "root" that legitimately owns a browser --
# matches both the main worker (`-Q default,scrape,...`) and the translate
# worker, so this stays correct if that pool is ever given browser access too.
_WORKER_MARKERS = ("celery", "worker")
_MAX_ANCESTOR_HOPS = 8
DEFAULT_MIN_AGE_SECONDS = 120


@dataclass(frozen=True)
class _Proc:
    pid: int
    ppid: int
    etimes: int
    cmd: str


def _list_processes() -> dict[int, _Proc]:
    """Every process visible to this user via `ps` -- no psutil dependency elsewhere in this codebase, and none needed for this one sweep."""
    result = subprocess.run(
        ["ps", "-eo", "pid,ppid,etimes,args", "--no-headers"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    procs: dict[int, _Proc] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=3)
        if len(parts) < 4:
            continue
        try:
            pid, ppid, etimes = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        procs[pid] = _Proc(pid=pid, ppid=ppid, etimes=etimes, cmd=parts[3])
    return procs


def _is_worker_root(cmd: str) -> bool:
    low = cmd.lower()
    return all(marker in low for marker in _WORKER_MARKERS)


def _traces_to_live_worker(pid: int, procs: dict[int, _Proc]) -> bool:
    """Walk the ppid chain a bounded number of hops. True if a live celery worker process is an ancestor -- meaning this browser tree still legitimately belongs to something."""
    seen: set[int] = set()
    current = pid
    for _ in range(_MAX_ANCESTOR_HOPS):
        if current <= 1 or current in seen:
            return False
        proc = procs.get(current)
        if proc is None:
            return False
        seen.add(current)
        if _is_worker_root(proc.cmd):
            return True
        current = proc.ppid
    return False


def _browser_tree_roots(procs: dict[int, _Proc]) -> list[_Proc]:
    """Playwright driver/node processes, plus any top-level chrome-headless-shell whose own parent ISN'T itself part of a chrome tree (covers the driver-already-gone case -- e.g. the driver exited cleanly on stdin EOF but its Chromium child, a separate session, did not)."""
    roots = []
    for p in procs.values():
        if _DRIVER_MARKER in p.cmd:
            roots.append(p)
            continue
        if _BROWSER_MARKER in p.cmd:
            parent = procs.get(p.ppid)
            parent_is_browser = parent is not None and (
                _DRIVER_MARKER in parent.cmd or _BROWSER_MARKER in parent.cmd
            )
            if not parent_is_browser:
                roots.append(p)
    return roots


def _descendants(root_pid: int, procs: dict[int, _Proc]) -> list[int]:
    children_by_parent: dict[int, list[int]] = {}
    for p in procs.values():
        children_by_parent.setdefault(p.ppid, []).append(p.pid)
    out: list[int] = []
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        for child in children_by_parent.get(pid, []):
            out.append(child)
            stack.append(child)
    return out


def reap_orphaned_browser_processes(
    *,
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS,
    dry_run: bool = False,
) -> dict[str, object]:
    """Find and SIGKILL Playwright/Chromium process trees with no live celery worker ancestor, old enough to rule out a mid-launch/mid-reparent race. Never raises -- a failed sweep just tries again next beat."""
    try:
        procs = _list_processes()
    except Exception:
        logger.warning("browser process reaper: failed to list processes", exc_info=True)
        return {"trees_killed": 0, "pids_killed": 0, "skipped_too_young": 0, "details": [], "error": True}

    killed: list[dict[str, object]] = []
    skipped_too_young = 0
    for root in _browser_tree_roots(procs):
        if root.etimes < min_age_seconds:
            skipped_too_young += 1
            continue
        if _traces_to_live_worker(root.pid, procs):
            continue
        pids_to_kill = [root.pid, *_descendants(root.pid, procs)]
        logger.warning(
            "browser process reaper: killing orphaned tree root pid=%s age=%ss cmd=%.160s (%d pids total)",
            root.pid,
            root.etimes,
            root.cmd,
            len(pids_to_kill),
        )
        if not dry_run:
            for pid in pids_to_kill:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGKILL)
        killed.append(
            {"root_pid": root.pid, "age_seconds": root.etimes, "pids_killed": pids_to_kill}
        )

    return {
        "trees_killed": len(killed),
        "pids_killed": sum(len(k["pids_killed"]) for k in killed),  # type: ignore[arg-type]
        "skipped_too_young": skipped_too_young,
        "details": killed,
    }
