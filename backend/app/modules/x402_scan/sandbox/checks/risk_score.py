"""Aggregates every check's own risk_score/caution_notes into one top-level verdict.

Deliberately dumb and check-agnostic: walks the assembled report looking for
any "risk_score"/"caution_notes" key at any nesting depth, so a brand new
check module needs zero registration here to participate -- just return
those two optional keys per the checks/__init__.py contract.

Takes the MAXIMUM risk_score found anywhere, not an average: one confirmed
ClamAV hit buried under otherwise-clean checks must not get diluted into a
"probably fine" score. This is a security tool -- the worst signal wins.
"""

from __future__ import annotations


def _walk(node: object) -> tuple[float, list[str]]:
    """(max risk_score, all caution_notes) found anywhere under `node`."""
    max_score = 0.0
    notes: list[str] = []
    if isinstance(node, dict):
        score = node.get("risk_score")
        if isinstance(score, (int, float)):
            max_score = max(max_score, float(score))
        own_notes = node.get("caution_notes")
        if isinstance(own_notes, list):
            notes.extend(str(n) for n in own_notes)
        for value in node.values():
            child_score, child_notes = _walk(value)
            max_score = max(max_score, child_score)
            notes.extend(child_notes)
    elif isinstance(node, list):
        for item in node:
            child_score, child_notes = _walk(item)
            max_score = max(max_score, child_score)
            notes.extend(child_notes)
    return max_score, notes


# The single machine-branchable boolean marketing-agent feedback asked for
# (2026-09-01): an agent consuming this endpoint shouldn't have to know
# "score >= 0.9 means malicious" is the undocumented threshold -- this is
# that documentation, expressed as a field instead of prose. Kept in sync
# with _verdict_for's own "likely malicious" cutoff by construction (see
# summarize below), not a second number that could drift from it.
_MALICIOUS_THRESHOLD = 0.9


def _verdict_for(score: float) -> str:
    if score >= _MALICIOUS_THRESHOLD:
        return "likely malicious"
    if score >= 0.5:
        return "suspicious -- review recommended"
    if score >= 0.15:
        return "low-risk signals present"
    return "no concerns found"


def summarize(report: dict) -> dict:
    """{"score", "verdict", "malicious", "caution_notes"} for the full assembled report."""
    score, notes = _walk(report)
    # Stable order, de-duplicated, without losing first-seen ordering.
    seen: set[str] = set()
    deduped = [n for n in notes if not (n in seen or seen.add(n))]
    return {
        "score": round(score, 3),
        "verdict": _verdict_for(score),
        "malicious": score >= _MALICIOUS_THRESHOLD,
        "caution_notes": deduped,
    }
