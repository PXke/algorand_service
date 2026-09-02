"""Runs INSIDE the sandbox container only. Static analysis of one file, no execution.

Invoked as `python3 /scan.py /scan/input`. Prints exactly one JSON object to
stdout and nothing else -- the host-side runner (services/sandbox_runner.py)
parses stdout as the whole report, so every diagnostic goes to stderr instead.

Deliberately does not execute, `import`, `exec`, unpack-and-run, or decode
the target with any library that has its own code-execution/full-decode
surface (e.g. no PIL.Image.load(), no zipfile-with-extractall-then-run).
Every check here reads bytes/headers and reports on them; nothing here runs
the input. (checks/image_bomb.py inspects an image's header dimensions only
via a non-decoding read -- opening a container format to read its declared
metadata is fine; decoding the raster/pixel data it describes is the thing
this rule forbids.)

Orchestrator only. Each individual check lives in its own module under
checks/ (see checks/__init__.py for the plugin contract) and every check
that applies to the top-level target runs CONCURRENTLY via a thread pool --
these are I/O- and subprocess-bound, not CPU-bound, so threads (not
processes) are the right tool and avoid the pickling overhead of IPC for
what's mostly "wait on a subprocess". checks/risk_score.py then aggregates
every check's own risk_score/caution_notes into one top-level verdict.

Tool set (v0/v1) and why each earns its place for an arbitrary uploaded
file/tarball -- see each module's own docstring for implementation detail:
  - checks/clamav.py: signature-based known-malware match. The one check
    here with a real "yes/no this is a known bad file" verdict.
  - checks/basic_analysis.py: `file`/libmagic type-vs-extension check,
    Shannon entropy (packed/encrypted payload signal), embedded URL/IP
    extraction (leads for a human to judge, not a verdict on its own).
  - checks/archive.py: zip/tar-bomb-safe member listing -- declared sizes
    read from the archive's own directory/header WITHOUT extracting; a
    member is only extracted (to a size-capped tmpfs) if the archive's own
    declared total stays under the configured caps.
  - checks/image_bomb.py: pixel/decompression-bomb detection via header-only
    inspection (declared dimensions vs file size), never decoding raster data.
  - checks/script_patterns.py: static regex detection of known-dangerous
    shell/script idioms (fork bombs, curl-pipe-shell, rm -rf /,
    base64-decode-exec chains, reverse shells) -- text matching only, never
    executes/sources any part of the target.

Deliberately NOT wired (designed, not built -- flagged in the final report
rather than half-built here):
  - oletools/olevba (Office macro auto-exec + suspicious-keyword detection)
  - pdfid (malicious-PDF keyword scan: /JavaScript /OpenAction /Launch)
  - pefile/lief (PE import-table heuristics), YARA (custom signature rules)
  - VirusTotal or any other hash-reputation lookup: needs network egress,
    which conflicts with this sandbox's --network none design by
    construction -- if ever wanted, it must happen HOST-SIDE after the
    container exits, on just the file's hash, never inside the sandbox.
  - Actual execution/detonation (running the file to observe behavior):
    explicitly out of scope -- see sandbox_runner.py's Docker-vs-Firecracker
    docstring for why that mode's risk profile reopens the isolation choice.
  - rkhunter: NOT included, deliberately. It's a live-system rootkit scanner
    (compares running processes, loaded kernel modules, and installed
    binaries against known-good hashes) -- there is no live system here,
    only a file.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    # Host-side (pytest imports this module as part of the real backend
    # package tree).
    from app.modules.x402_scan.sandbox.checks import (
        archive,
        basic_analysis,
        clamav,
        exif_metadata,
        fuzzy_hash,
        image_bomb,
        risk_score,
        script_patterns,
        yara_scan,
    )
except ImportError:
    # Inside the sandbox container, scan.py runs standalone as `python3
    # /scan.py` with no `app` package on the path at all -- only /scan.py
    # and /checks (see the Dockerfile's COPY) exist. checks/*.py use
    # relative imports internally so the same files resolve correctly
    # whichever way this top-level package ends up named.
    from checks import (  # type: ignore[no-redef]
        archive,
        basic_analysis,
        clamav,
        exif_metadata,
        fuzzy_hash,
        image_bomb,
        risk_score,
        script_patterns,
        yara_scan,
    )

INPUT_PATH = sys.argv[1] if len(sys.argv) > 1 else "/scan/input"

# (report key, check function) -- every entry here runs concurrently against
# the same top-level target path. Adding a new check module means adding one
# line here; nothing else in this file changes.
CHECKS: list[tuple[str, object]] = [
    ("clamav", clamav.run),
    ("target", basic_analysis.run),
    ("archive", archive.run),
    ("image_bomb", image_bomb.run),
    ("script_patterns", script_patterns.run),
    ("yara", yara_scan.run),
    ("fuzzy_hash", fuzzy_hash.run),
    ("exif_metadata", exif_metadata.run),
]


def _run_all(path: str) -> dict:
    """Run every registered check concurrently; a check that raises reports {"error": ...}."""
    result: dict = {}
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        futures = {pool.submit(fn, path): key for key, fn in CHECKS}
        for future, key in [(f, futures[f]) for f in futures]:
            try:
                value = future.result()
            except Exception as exc:  # a check's own bug must not sink the whole scan
                value = {"error": str(exc)}
            if value:  # archive.run() returns {} for a non-archive target -- omit the key
                result[key] = value
    return result


def _scan_status(result: dict) -> str:
    """Returns "ok" unless at least one registered check's own top-level dict carries an "error" key.

    Feedback from an agent consuming this endpoint (2026-09-01): there was no
    way to tell "clean" apart from "couldn't scan" -- a check that failed
    internally (e.g. clamscan OOM-killed, checks/clamav.py's own size-limit
    guard) only ever showed up buried in that one check's own sub-object.
    Deliberately only inspects each check's DIRECT dict (not the full
    _walk-style recursion risk_score.py does) -- "did the checks we asked
    for actually run" is a flatter question than "is there a risk signal
    anywhere," and a nested sub-check failure (e.g. one archive member
    checks/basic_analysis.py couldn't read) is already visible in that
    member's own entry without needing to flip this top-level flag.
    """
    for key, _fn in CHECKS:
        value = result.get(key)
        if isinstance(value, dict) and "error" in value:
            return "degraded"
    return "ok"


# Friendly display names for the one-line human summary -- CHECKS' own keys
# are the wire/JSON field names (stable API), these are cosmetic only.
_DISPLAY_NAMES = {
    "clamav": "ClamAV",
    "target": "file analysis",
    "archive": "archive contents",
    "image_bomb": "image bomb check",
    "script_patterns": "script patterns",
    "yara": "YARA",
    "fuzzy_hash": "fuzzy hash",
    "exif_metadata": "metadata",
}


def _one_line_summary(result: dict, risk: dict) -> str:
    """Human-readable one-liner for a chat/UI context, e.g. "Suspicious -- 2 indicators (ClamAV, YARA)".

    Only counts a check as a contributing "indicator" if its OWN dict
    carries a risk_score, mirroring _scan_status's direct-only inspection --
    a check nested two levels down (an archive member) isn't named here,
    only surfaced via the aggregate score/verdict, to keep this genuinely
    one line.
    """
    hits = [
        _DISPLAY_NAMES.get(key, key)
        for key, _fn in CHECKS
        if isinstance(result.get(key), dict) and result[key].get("risk_score")
    ]
    verdict = risk["verdict"].split(" -- ")[0].capitalize()
    target_type = (
        result.get("target", {}).get("type") if isinstance(result.get("target"), dict) else None
    )
    parts = [verdict]
    if hits:
        parts.append(f"{len(hits)} indicator(s) ({', '.join(hits)})")
    else:
        parts.append("no indicators")
    if target_type:
        parts.append(f"file type: {target_type}")
    return " -- ".join(parts)


def main() -> None:
    """Scan INPUT_PATH and print exactly one JSON report to stdout (see module docstring)."""
    target = Path(INPUT_PATH)
    if not target.is_file():
        print(  # noqa: T201 -- stdout IS the report
            json.dumps(
                {"error": f"no such file: {INPUT_PATH}", "schema_version": 1, "status": "error"}
            )
        )
        return

    result = _run_all(INPUT_PATH)
    result["risk"] = risk_score.summarize(result)
    result["schema_version"] = 1
    result["status"] = _scan_status(result)
    result["one_line_summary"] = _one_line_summary(result, result["risk"])
    print(json.dumps(result))  # noqa: T201 -- stdout IS the report


if __name__ == "__main__":
    main()
