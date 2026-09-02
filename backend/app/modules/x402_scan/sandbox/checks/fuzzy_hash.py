"""ssdeep fuzzy/context-triggered piecewise hashing of the target file.

Computes a ssdeep ("Context Triggered Piecewise Hashing") signature for the
target -- a fuzzy fingerprint where similar-but-not-identical files produce
similar-but-not-identical hashes, unlike a cryptographic hash where a single
changed byte scrambles the whole digest. This is useful even with nothing to
compare against here and now: it's a stable value a caller can later match
against their own known-bad corpus (a different sample of the same malware
family, a modified copy of a known-bad file, etc.) without this sandbox
needing to store or ship any reference samples itself.

v0/v1 comparison-corpus decision: this check does NOT bundle any known-bad
ssdeep hashes to compare against. A small, safely-licensed, conservatively
sourced reference corpus of known-bad-file ssdeep hashes was not readily
available -- real malware-sample hashes come from threat-intel feeds with
handling/licensing requirements this sandbox has no business taking on, and
bundling anything found by ad-hoc search is a worse outcome than shipping
this check without a corpus. This is a deliberate v2 item: wire a
`compare(signature, corpus)` helper (not written yet) up to a properly
sourced corpus later. Until then this check only ever reports the computed
hash and never sets risk_score -- absence of a corpus is not evidence of
absence of risk, so it must not manufacture a false "clean" opinion.

Shells out to the `ssdeep` CLI (Alpine apk package `ssdeep`) rather than the
`py3-ssdeep` binding. Both were verified available for Alpine 3.20: the CLI
package alone pulls in only `libfuzzy2`, while the Python binding
additionally pulls in py3-cffi/py3-six/py3-cparser -- more installed
packages than this sandbox's minimal-attack-surface design (see the
Dockerfile's own comment) needs for one hash computation. Same subprocess
pattern as checks/clamav.py.
"""

from __future__ import annotations

import subprocess

SSDEEP_TIMEOUT_SECONDS = 60


def _parse_ssdeep_output(stdout: str) -> str | None:
    """Extract the bare `blocksize:hash:hash` signature from `ssdeep -b`'s stdout, or None.

    `ssdeep -b <path>` prints a fixed CSV header line, then one line per file:
    `<signature>,"<filename>"` where signature is `blocksize:hash:hash`.
    ssdeep signature characters are decimal digits, ':', and the base64
    alphabet only -- never a comma -- so splitting off the last
    comma-separated field is always safe, regardless of what the filename
    (unused here) contains.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    signature = lines[1].rsplit(",", 1)[0].strip()
    return signature or None


def run(path: str) -> dict:
    """Compute the ssdeep fuzzy hash of `path`. Report-only: never sets risk_score (see docstring)."""
    try:
        proc = subprocess.run(
            ["ssdeep", "-b", path],
            capture_output=True,
            text=True,
            timeout=SSDEEP_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return {"error": str(exc)}

    signature = _parse_ssdeep_output(proc.stdout)
    if signature is None:
        return {
            "error": (
                f"ssdeep produced no parseable signature "
                f"(exit={proc.returncode}, stderr={proc.stderr.strip()[:500]})"
            )
        }
    return {
        "ssdeep_hash": signature,
        "comparison_corpus": None,
        # Deliberately never sets risk_score -- confirmed by an outside
        # reviewer's feedback (2026-09-01) that this needed to be explicit,
        # not just true by omission: with no corpus bundled, this hash is
        # informational only and MUST NOT influence checks/risk_score.py's
        # aggregate verdict. A caller wanting fuzzy-match detection matches
        # this hash against their own corpus themselves.
        "note": (
            "no known-bad comparison corpus is bundled (v2 item -- see module docstring); "
            "informational only, never contributes to risk_score -- match this hash against "
            "your own corpus if you need fuzzy-match detection"
        ),
    }
