"""File-type verification, entropy, and embedded URL/IP extraction -- see package docstring."""

from __future__ import annotations

import math
import re
import subprocess
from collections import Counter
from pathlib import Path

MAX_PREVIEW_BYTES = 20 * 1024 * 1024
STRINGS_MAX_MATCHES = 200
# Near-8 bits/byte across a file that isn't already a known-compressed format
# is the standard packed/encrypted payload signal.
HIGH_ENTROPY_THRESHOLD = 7.5

_URL_RE = re.compile(rb"https?://[^\s\"'<>]{4,200}")
_IPV4_RE = re.compile(
    rb"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
)


def _shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def _extract_indicators(data: bytes) -> dict:
    urls = sorted({m.decode("latin1", "replace") for m in _URL_RE.findall(data)})[
        :STRINGS_MAX_MATCHES
    ]
    ips = sorted({m.decode("ascii") for m in _IPV4_RE.findall(data)})[:STRINGS_MAX_MATCHES]
    return {"urls": urls, "ipv4_addresses": ips}


def _file_type(path: str) -> str:
    try:
        proc = subprocess.run(["file", "-b", path], capture_output=True, text=True, timeout=10)
        return proc.stdout.strip()
    except Exception as exc:
        return f"(file(1) failed: {exc})"


def run(path: str, *, label: str | None = None) -> dict:
    """Type, entropy, and embedded-indicator report for one file (not a directory).

    Reads at most MAX_PREVIEW_BYTES off disk, not the whole file -- security
    audit finding: `read_bytes()[:MAX_PREVIEW_BYTES]` pulled the ENTIRE file
    into memory (up to x402_scan_max_download_bytes, 1GB) before slicing,
    letting a large high-entropy input OOM the container after payment had
    already been taken. `open().read(cap)` only ever reads the cap.
    """
    p = Path(path)
    with p.open("rb") as fh:
        data = fh.read(MAX_PREVIEW_BYTES)
    entropy = round(_shannon_entropy(data), 3)
    indicators = _extract_indicators(data)
    result = {
        "label": label or p.name,
        "size_bytes": p.stat().st_size,
        "type": _file_type(path),
        "entropy_bits_per_byte": entropy,
        "indicators": indicators,
    }
    caution_notes = []
    risk_score = 0.0
    if entropy >= HIGH_ENTROPY_THRESHOLD:
        risk_score = max(risk_score, 0.3)
        caution_notes.append(
            f"high entropy ({entropy} bits/byte) -- possibly packed or encrypted content"
        )
    if indicators["urls"] or indicators["ipv4_addresses"]:
        risk_score = max(risk_score, 0.15)
        caution_notes.append(
            f"embedded network references found: {len(indicators['urls'])} URL(s), "
            f"{len(indicators['ipv4_addresses'])} IPv4 address(es) -- not proof of anything by "
            "itself, but worth a human look"
        )
    if caution_notes:
        result["risk_score"] = risk_score
        result["caution_notes"] = caution_notes
    return result
