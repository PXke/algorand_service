"""Zip/tar-bomb-safe archive member listing -- see package docstring."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

# Relative import deliberately: this module is imported both as
# app.modules.x402_scan.sandbox.checks.archive (host-side pytest) and as a
# bare top-level `checks` package inside the sandbox container (see
# scan.py's import fallback and the Dockerfile's COPY of checks/) -- a
# relative import resolves correctly under either package name.
from . import basic_analysis, clamav

MAX_EXTRACT_BYTES = 200 * 1024 * 1024
MAX_EXTRACT_MEMBERS = 2000


def _member_plan(path: str) -> tuple[str, list[tuple[str, int]]] | None:
    """(kind, [(member_name, uncompressed_size), ...]) for a zip/tar, else None. Never extracts."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            return "zip", [(i.filename, i.file_size) for i in zf.infolist()]
    try:
        if tarfile.is_tarfile(path):
            with tarfile.open(path) as tf:
                return "tar", [(m.name, m.size) for m in tf.getmembers() if m.isfile()]
    except Exception:
        return None
    return None


def run(path: str) -> dict:
    """None (not an archive) short-circuits to {}; else the bomb-safe member report."""
    plan = _member_plan(path)
    if plan is None:
        return {}
    kind, members = plan
    return _scan_archive_internal(path, kind, members)


def _scan_archive_internal(path: str, kind: str, members: list[tuple[str, int]]) -> dict:
    """Split out from run() so the bomb-guard cap check is testable without a real archive on disk."""
    total = sum(size for _, size in members)
    report: dict = {
        "archive_kind": kind,
        "member_count": len(members),
        "declared_uncompressed_bytes": total,
        "members": [],
        "extraction_skipped_reason": None,
    }
    if len(members) > MAX_EXTRACT_MEMBERS or total > MAX_EXTRACT_BYTES:
        report["extraction_skipped_reason"] = (
            f"declared size/member-count exceeds sandbox caps "
            f"(members={len(members)}/{MAX_EXTRACT_MEMBERS}, "
            f"bytes={total}/{MAX_EXTRACT_BYTES}) -- refusing to extract (zip/tar-bomb guard)"
        )
        report["members"] = [name for name, _ in members[:50]]
        report["risk_score"] = 0.4
        report["caution_notes"] = [
            "archive declares more content than the sandbox will extract -- could not be "
            "fully inspected, treat as unverified rather than clean"
        ]
        return report

    extract_dir = Path("/tmp/scan_extract")
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "zip":
            with zipfile.ZipFile(path) as zf:
                zf.extractall(extract_dir)
        else:
            with tarfile.open(path) as tf:
                tf.extractall(extract_dir, filter="data")
    except Exception as exc:
        report["extraction_skipped_reason"] = f"extraction failed: {exc}"
        report["risk_score"] = 0.2
        report["caution_notes"] = [f"archive could not be extracted for inspection: {exc}"]
        return report

    clam_result = clamav.run(str(extract_dir))
    report["clamav"] = clam_result
    member_reports = []
    for fpath in extract_dir.rglob("*"):
        if not fpath.is_file():
            continue
        rel = str(fpath.relative_to(extract_dir))
        try:
            member_reports.append(basic_analysis.run(str(fpath), label=rel))
        except Exception as exc:
            member_reports.append({"label": rel, "error": str(exc)})
    report["members"] = member_reports

    if clam_result.get("risk_score"):
        report["risk_score"] = clam_result["risk_score"]
        report["caution_notes"] = clam_result.get("caution_notes", [])
    return report
