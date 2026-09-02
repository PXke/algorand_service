"""YARA pattern-match scan against a curated signature-base ruleset -- see package docstring.

A second opinion independent of checks/clamav.py: ClamAV matches known-bad
file hashes/byte-signatures from its own DB, YARA matches hand-written
content/structure rules (byte sequences, string sets, PE-header heuristics)
compiled from Florian Roth's community "signature-base" project
(https://github.com/Neo23x0/signature-base). A YARA hit and a ClamAV hit are
independent signals -- neither substitutes for the other, which is why this
is its own check module rather than folded into clamav.py.

Shells out to the `yara` CLI binary against a PRE-COMPILED ruleset baked
into the sandbox image at build time (see the Dockerfile addendum in this
task's final report) -- never git-clones or fetches rules at container-RUN
time, same "no network at request time" rule the ClamAV DB bake already
follows. `yara-python` (pip) was deliberately not used: it is not available
as an Alpine apk package (only the `yara` CLI + C library are), and
building it from source via pip on musl libc is exactly the kind of
possibly-fragile pip/musl dependency this sandbox's Dockerfile comment
already argues against taking on for ClamAV/Pillow -- shelling out to a
CLI binary (same pattern as checks/clamav.py's `clamscan` call) avoids
that risk entirely for a marginal subprocess-parsing cost.

Ruleset subset (NOT the full signature-base repo -- see the final report for
the full rationale): apt_*.yar (APT malware-family signatures) + gen_*.yar
minus a handful of files that declare THOR/LOKI external variables this
sandbox does not define (filename/filepath/extension/etc. -- compiling them
without those externals declared fails the whole `yarac` build, so they are
excluded rather than worked around) + the webshell-specific files (
thor-webshells.yar, cn_pentestset_webshells.yar, webshell_*.yar) + hacktool
detection (thor-hacktools.yar, hktl_*.yar) + general_officemacros.yar.
Excluded: crime_*/mal_*/expl_*/exploit_*/vul_*/vuln_* (campaign- and
CVE-specific, narrower value for an arbitrary-file scanner than the
generic/apt/webshell/hacktool buckets) and thor_inverse_matches.yar (a
false-positive SUPPRESSION list, not a detection ruleset -- a "match" there
means "known-good", the opposite of every other rule file here, so it is
never compiled into this ruleset at all to avoid ever mis-reporting one of
its hits as a caution note).
"""

from __future__ import annotations

import os
import re
import subprocess

# Compiled ruleset baked into the image at build time (see this task's final
# report for the exact Dockerfile RUN/COPY steps). Overridable via env var
# purely so this module is testable/runnable outside the container without
# editing source; the sandbox image itself always sets/relies on the default.
RULES_PATH = os.environ.get("YARA_RULES_PATH", "/opt/yara-rules/signature-base.yarc")

YARA_TIMEOUT_SECONDS = 120
# A real hit list from a curated ruleset is expected to be short; cap
# defensively so a pathological target matching hundreds of rules can't
# blow up the report (same spirit as basic_analysis.STRINGS_MAX_MATCHES).
MAX_REPORTED_MATCHES = 50

# One result line per matched rule, printed by `yara -C -m -w <rules> <path>`:
#   RuleName [meta_key1=value1,meta_key2="value 2"] /path/to/target
_LINE_RE = re.compile(r"^(?P<rule>\S+)\s+\[(?P<meta>.*)\]\s+(?P<path>.+)$")
# One key=value pair inside the bracketed meta block. Quoted values are
# matched non-greedily up to the next unescaped quote (so a description
# containing a comma doesn't split into two bogus pairs); bareword values
# (ints/bools) run up to the next comma or the closing bracket.
_META_RE = re.compile(r'(?P<key>\w+)=(?:"(?P<qval>(?:[^"\\]|\\.)*)"|(?P<val>[^,\]]+))')

# signature-base's THOR-style convention: an integer 0-100 confidence/severity
# score in each rule's `score` meta field (observed range in the curated
# subset: 60-100). Rules without a score meta still get a real, but slightly
# lower, floor -- a named signature-base match is a specific signal even with
# no declared score, just not as calibrated as a scored one.
_SCORE_META_MIN = 0.45
_SCORE_META_MAX = 0.98
_UNSCORED_MATCH_RISK = 0.75
# signature-base names its broadest, most FP-prone heuristics with a
# gen(eric)_ prefix by convention; treat those as lower-confidence than a
# specific named/APT/webshell/hacktool rule when no score meta is present.
_GENERIC_UNSCORED_MATCH_RISK = 0.55


def _parse_meta(meta_str: str) -> dict[str, str]:
    """{"key": "value", ...} parsed from one bracketed `-m` meta block."""
    meta: dict[str, str] = {}
    for m in _META_RE.finditer(meta_str):
        if m.group("qval") is not None:
            value = m.group("qval").replace('\\"', '"').replace("\\\\", "\\")
        else:
            value = m.group("val").strip()
        meta[m.group("key")] = value
    return meta


def _parse_yara_output(stdout: str) -> list[dict]:
    """[{"rule": str, "meta": {...}}, ...] parsed from `yara -m` stdout, one entry per match line."""
    matches = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if m:
            matches.append({"rule": m.group("rule"), "meta": _parse_meta(m.group("meta"))})
        else:
            # Unexpected format -- keep the raw line as a low-detail match
            # rather than silently dropping a real hit (CLAUDE.md: empty is
            # not "none found").
            matches.append({"rule": line, "meta": {}})
    return matches


def _risk_score_for_match(rule_name: str, meta: dict[str, str]) -> float:
    """This match's own risk_score, using the rule's `score` meta when present."""
    raw_score = meta.get("score")
    if raw_score is not None:
        try:
            numeric = float(raw_score)
        except ValueError:
            numeric = None
        if numeric is not None:
            # signature-base scores are on a 0-100 scale; normalize, then
            # clamp into a range that still leaves room for a confirmed
            # ClamAV hit (1.0) to read as the more certain signal.
            normalized = numeric / 100.0 if numeric > 1.0 else numeric
            return round(min(max(normalized, _SCORE_META_MIN), _SCORE_META_MAX), 3)
    if rule_name.lower().startswith("gen_") or "generic" in rule_name.lower():
        return _GENERIC_UNSCORED_MATCH_RISK
    return _UNSCORED_MATCH_RISK


def _note_for_match(match: dict) -> str:
    rule = match["rule"]
    description = match["meta"].get("description")
    if description:
        return f"YARA match: {rule} -- {description}"
    return f"YARA match: {rule}"


def run(path: str) -> dict:
    """Run the compiled signature-base ruleset against `path`. No match -> no risk_score key."""
    try:
        proc = subprocess.run(
            ["yara", "-C", "-m", "-w", RULES_PATH, path],
            capture_output=True,
            text=True,
            timeout=YARA_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # Includes FileNotFoundError if the `yara` binary/ruleset isn't
        # present in whatever environment this runs in -- never crash the
        # whole scan, and never silently report clean (no risk_score key at
        # all, distinguishable from "ran and found nothing" by "error" key).
        return {"error": str(exc)}

    # yara CLI exit codes: 0 = ran successfully (matches or none), non-zero =
    # a real error (missing/corrupt compiled ruleset, unreadable target, bad
    # invocation). -w suppresses warnings so stderr on a clean run is empty.
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        return {"error": f"yara failed: {detail}"}

    matches = _parse_yara_output(proc.stdout)
    if not matches:
        return {"matched_rules": 0}

    truncated = matches[:MAX_REPORTED_MATCHES]
    result: dict = {
        "matched_rules": len(matches),
        "matches": [
            {"rule": m["rule"], "description": m["meta"].get("description")} for m in truncated
        ],
        "risk_score": max(_risk_score_for_match(m["rule"], m["meta"]) for m in truncated),
        "caution_notes": [_note_for_match(m) for m in truncated],
    }
    return result
