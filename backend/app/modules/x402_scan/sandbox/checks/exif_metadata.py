"""ExifTool-based embedded metadata inspection for images/PDFs/documents.

Catches a real gap the entropy/string/type checks don't: malformed or
suspicious *embedded metadata*, as opposed to the file's raw content. Runs
`exiftool -j` (Alpine apk package `exiftool`) against the target and flags:

  - malformed metadata segments -- exiftool's own Warning/Error output on an
    otherwise-recognized image/PDF/document ("JPEG format error", "Invalid
    xref table", etc.) is exactly this signal, verified empirically: a
    syntactically-recognized-but-broken file surfaces `FileType` (so it's in
    scope) *and* a Warning/Error key exiftool would never emit for a clean
    file of the same type.
  - anomalously oversized metadata fields -- a single tag's value far larger
    than any legitimate EXIF/XMP/IPTC/PDF-Info field has any reason to be.
  - embedded executable/script-looking content inside a metadata field's
    text value (e.g. a shebang, `<script`, `eval(`, `javascript:`).
  - known dangerous PDF action/script keys (`/OpenAction`, `/JavaScript`,
    `/JS`, `/Launch`, `/AA`, `/RichMedia`, `/EmbeddedFile`, `/URI`) *if*
    exiftool surfaces them as tags -- verified empirically that exiftool's
    default tag set generally does NOT expose these (they're PDF structure,
    not metadata exiftool extracts by default), so this branch is a cheap
    defensive check, not the primary signal. A dedicated PDF
    structure/action scanner (pdfid-style) is deliberately NOT this check's
    job -- see scan.py's module docstring for why it isn't wired at all yet.

Deliberately does NOT flag GPS coordinates, author/creator fields, or other
privacy-relevant-but-not-security-relevant metadata -- that's a different
concern this check has no opinion on.

Only reports on targets exiftool actually identifies as an image, PDF, or
common office/document format (APPLICABLE_FILE_TYPES below); for anything
else (exiftool reports "Unknown file type", the file is empty, or it's some
other recognized-but-out-of-scope format like an executable or archive --
those are covered by other checks) this returns `{}`, mirroring
checks/archive.py's convention for a non-applicable target.

Every reported field value is length-capped so this can never become a way
to smuggle an oversized blob into the report (the oversized-field *signal*
is still raised -- only the *value actually embedded in the JSON report* is
truncated). Generic filesystem/tool-identity fields exiftool always emits
for every file (FileName, FileSize, dates, FileType, MIMEType, ...) are
excluded from the reported fields -- checks/basic_analysis.py already covers
file-type identification; this check's whole point is what's *underneath*
that, in the file's own embedded metadata.
"""

from __future__ import annotations

import json
import re
import subprocess

EXIFTOOL_TIMEOUT_SECONDS = 30

# Values chosen to be generous for legitimate metadata (a full XMP packet or
# an embedded ICC profile description can legitimately run a few hundred to
# low-thousands of characters) while still capping what actually lands in
# the JSON report and separately flagging what's outright anomalous.
FIELD_VALUE_DISPLAY_CAP = 500
FIELD_VALUE_OVERSIZED_THRESHOLD = 4096
MAX_FIELDS_REPORTED = 60

# exiftool emits these for literally every file it can identify at all --
# they're filesystem/tool-identity noise, not the file's own embedded
# metadata, and FileType/MIMEType duplicate checks/basic_analysis.py's job.
_BOILERPLATE_KEYS = frozenset(
    {
        "SourceFile",
        "ExifToolVersion",
        "FileName",
        "Directory",
        "FileSize",
        "FileModifyDate",
        "FileAccessDate",
        "FileInodeChangeDate",
        "FilePermissions",
        "FileType",
        "FileTypeExtension",
        "MIMEType",
        "MIMEEncoding",
        "Newlines",
        "LineCount",
        "WordCount",
    }
)

# exiftool's `FileType` value for every format this check considers "an
# image/PDF/document" worth inspecting embedded metadata on. Anything else
# (executables, archives, unrecognized binaries, ...) is out of this check's
# scope -- other checks cover those, and exiftool has nothing security-
# relevant to say about e.g. a zip's "metadata."
APPLICABLE_FILE_TYPES = frozenset(
    {
        # images
        "JPEG",
        "PNG",
        "GIF",
        "BMP",
        "TIFF",
        "WEBP",
        "HEIC",
        "HEIF",
        "ICO",
        "PSD",
        "SVG",
        "AVIF",
        "JP2",
        # PDF and office/document formats
        "PDF",
        "DOC",
        "DOCX",
        "XLS",
        "XLSX",
        "PPT",
        "PPTX",
        "RTF",
        "ODT",
        "ODS",
        "ODP",
        "EPS",
        "AI",
    }
)

# PDF action/script keys that would indicate auto-executing content, IF
# exiftool ever surfaces them as a tag name (see module docstring -- it
# generally does not by default; this is a defensive belt-and-suspenders
# check, not the primary signal).
_DANGEROUS_TAG_NAMES = frozenset(
    {
        "openaction",
        "javascript",
        "js",
        "launch",
        "aa",
        "richmedia",
        "embeddedfile",
        "uri",
    }
)

_SUSPICIOUS_CONTENT_PATTERNS = [
    re.compile(r"<script[\s>]", re.IGNORECASE),
    re.compile(r"#!\s*/(?:usr/)?bin/(?:env\s+)?(?:ba|da)?sh\b"),
    re.compile(r"\bpowershell(?:\.exe)?\b", re.IGNORECASE),
    re.compile(r"\bcmd(?:\.exe)?\s+/c\b", re.IGNORECASE),
    re.compile(r"\beval\s*\("),
    re.compile(r"javascript:", re.IGNORECASE),
]


def _prepare_value(value: object) -> tuple[object, int]:
    """(display_value, original_length) -- truncates long text so a huge field can't ride along whole."""
    if isinstance(value, (int, float, bool)) or value is None:
        return value, len(str(value))
    text = value if isinstance(value, str) else str(value)
    original_len = len(text)
    if original_len > FIELD_VALUE_DISPLAY_CAP:
        return text[
            :FIELD_VALUE_DISPLAY_CAP
        ] + f"...[truncated, {original_len} chars total]", original_len
    return text, original_len


def _content_flags(tag: str, value: object) -> list[str]:
    """Caution notes for one tag: dangerous PDF action-key name and/or script-looking value text."""
    notes = []
    if tag.lower() in _DANGEROUS_TAG_NAMES:
        notes.append(f"metadata field '{tag}' is a known auto-executing PDF action/script key name")
    text = value if isinstance(value, str) else None
    if text:
        for pattern in _SUSPICIOUS_CONTENT_PATTERNS:
            if pattern.search(text):
                notes.append(f"metadata field '{tag}' contains script/executable-looking content")
                break
    return notes


def _run_exiftool(path: str) -> tuple[dict | None, str | None]:
    """(raw exiftool dict, None) on success, or (None, error message) -- never raises."""
    try:
        proc = subprocess.run(
            ["exiftool", "-j", path],
            capture_output=True,
            text=True,
            timeout=EXIFTOOL_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return None, str(exc)

    try:
        parsed = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        stderr_tail = proc.stderr.strip()[:500]
        return None, (
            f"exiftool produced no parseable JSON (exit={proc.returncode}, stderr={stderr_tail})"
        )
    if not parsed or not isinstance(parsed, list) or not isinstance(parsed[0], dict):
        return None, "exiftool returned an empty or unexpected result"
    return parsed[0], None


def _malformed_structure_note(raw: dict, file_type: str) -> str | None:
    """A caution note if exiftool itself flagged Warning/Error on a recognized file, else None."""
    malformed = [
        f"{key}: {value}"
        for key, value in raw.items()
        if key.startswith("Warning") or key.startswith("Error")
    ]
    if not malformed:
        return None
    return (
        f"exiftool reported malformed/anomalous structure for a recognized {file_type} file: "
        + "; ".join(malformed)
    )


def _collect_metadata_fields(raw: dict) -> tuple[dict, int, list[str], float]:
    """(fields, omitted_count, caution_notes, risk_score) for raw's non-boilerplate tags."""
    metadata_fields: dict = {}
    truncated_field_count = 0
    caution_notes: list[str] = []
    risk_score = 0.0

    for key, value in raw.items():
        if key in _BOILERPLATE_KEYS or key.startswith("Warning") or key.startswith("Error"):
            continue
        if len(metadata_fields) >= MAX_FIELDS_REPORTED:
            truncated_field_count += 1
            continue

        display_value, original_len = _prepare_value(value)
        metadata_fields[key] = display_value

        if original_len > FIELD_VALUE_OVERSIZED_THRESHOLD:
            risk_score = max(risk_score, 0.3)
            caution_notes.append(
                f"metadata field '{key}' is anomalously large ({original_len} chars) -- "
                "not typical of a well-formed file's metadata block"
            )

        for note in _content_flags(key, value):
            risk_score = max(risk_score, 0.6 if "action/script key" in note else 0.5)
            caution_notes.append(note)

    return metadata_fields, truncated_field_count, caution_notes, risk_score


def run(path: str) -> dict:
    """ExifTool metadata report for `path`; `{}` if exiftool has nothing in-scope to say (see docstring)."""
    raw, error = _run_exiftool(path)
    if error is not None:
        return {"error": error}

    file_type = raw.get("FileType")
    if file_type not in APPLICABLE_FILE_TYPES:
        # Unknown/empty/out-of-scope target (e.g. exiftool's own "Unknown
        # file type" / "File is empty" errors leave FileType absent) --
        # nothing meaningful to report, mirrors archive.py's {} convention.
        return {}

    caution_notes: list[str] = []
    risk_score = 0.0

    malformed_note = _malformed_structure_note(raw, file_type)
    if malformed_note is not None:
        risk_score = max(risk_score, 0.3)
        caution_notes.append(malformed_note)

    metadata_fields, truncated_field_count, field_notes, field_risk = _collect_metadata_fields(raw)
    caution_notes.extend(field_notes)
    risk_score = max(risk_score, field_risk)

    result: dict = {
        "file_type": file_type,
        "metadata_fields": metadata_fields,
    }
    if truncated_field_count:
        result["fields_omitted"] = truncated_field_count
    if caution_notes:
        result["risk_score"] = risk_score
        result["caution_notes"] = caution_notes
    return result
