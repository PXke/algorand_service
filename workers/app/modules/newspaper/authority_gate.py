"""Deterministic gate against unattributed appeals to authority.

Root-caused 2026-07-18 (Algorand quantum-rebrand article, pre-release): the
writer asserted "industry-wide research suggests that Falcon signatures can
be 10-100x slower to verify than classical ECC signatures" — a fabricated
benchmark, wrong in *direction* (Falcon verification is fast; signing is the
costly operation), wearing a vague-authority costume no reader can check.
The same compose session's research trace contained the Foundation's real
size table, so this wasn't a research gap — it was decorative authority
invented at write time.

The rule: phrases like "industry research suggests", "experts say",
"studies show" are unattributable by construction — if the claim were real,
the writer's own research trace would hold a citable source, and the prose
should name it. Findings are fed into the revision loop first (the writer
can name the source or delete the claim); anything that survives revision is
excised sentence-by-sentence as a backstop, recorded on the payload so the
persisted final_output stays auditable — same self-healing shape as
quote_gate (de-quote) and link_gate (delink).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Clearly weaselly constructions only: each is an authority claim with no
# nameable authority. Deliberately NOT matched: named attributions ("the
# Foundation's roadmap states", "according to NIST"), plain uses of the
# nouns ("Algorand's published research on VRF"), first-person hedges.
_AUTHORITY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bindustry[- ]wide research\b",
        r"\bindustry (?:research|consensus|observers?|experts?)\s+"
        r"(?:suggests?|shows?|indicates?|agrees?|believes?|warns?)\b",
        r"\bexperts?\s+(?:say|agree|believe|suggest|warn|note|caution)\b",
        r"\banalysts?\s+(?:say|believe|expect|predict|suggest|warn|estimate)\b",
        r"\bstudies\s+(?:show|suggest|indicate|have shown|confirm)\b",
        r"\bresearch\s+(?:suggests?|shows?|indicates?|confirms?)\b",
        r"\bbenchmarks?\s+(?:indicate|suggest|show|reveal)\b",
        r"\bit is widely (?:believed|considered|accepted|known|understood)\b",
        r"\bobservers\s+(?:say|note|believe|suggest)\b",
        r"\b(?:many|some)\s+(?:believe|argue|estimate)\b",
        r"\b(?:unnamed |anonymous )?sources\s+(?:say|claim|suggest|indicate)\b",
    )
)

# Sentence boundary for the excision backstop: conservative split that leaves
# markdown structure (headings, table rows, list markers) untouched.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_MAX_EXCISED_SENTENCE_CHARS = 500


def find_unattributed_authority(body: str) -> list[str]:
    """Matched authority phrases, deduplicated, lowercase. Overlapping matches
    collapse to the earliest pattern's span ("industry-wide research suggests"
    is one finding, not also a "research suggests" finding inside it) so one
    weasel construction produces one revision-feedback line."""
    spans: list[tuple[int, int]] = []
    found: list[str] = []
    seen: set[str] = set()
    for pattern in _AUTHORITY_PATTERNS:
        for match in pattern.finditer(body or ""):
            start, end = match.span()
            if any(start < e and s < end for s, e in spans):
                continue
            spans.append((start, end))
            phrase = match.group(0).lower()
            if phrase not in seen:
                seen.add(phrase)
                found.append(phrase)
    return found


def authority_revision_issues(body: str) -> list[str]:
    """Revision-loop feedback lines, one per finding — same contract as the
    dead-link / chain-entity feedback in _review_and_revise."""
    return [
        f"unattributed authority: '{phrase}' — a claim only counts if YOUR "
        "research trace holds its source; name that specific source in the "
        "prose, or delete the claim entirely. Never launder an assertion "
        "through vague experts/studies/research no reader can check"
        for phrase in find_unattributed_authority(body)
    ]


def _excise_from_prose(body: str, removed: list[str]) -> str:
    """Remove whole sentences still carrying an authority phrase. Markdown
    structure lines (headings, tables, lists, images) are never touched —
    the phrases live in prose, and structural edits aren't worth the risk."""
    out_lines: list[str] = []
    for line in body.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(("#", "|", "-", "*", "!", ">")) or not stripped:
            out_lines.append(line)
            continue
        sentences = _SENTENCE_SPLIT_RE.split(line)
        kept: list[str] = []
        for sentence in sentences:
            if len(sentence) <= _MAX_EXCISED_SENTENCE_CHARS and any(
                p.search(sentence) for p in _AUTHORITY_PATTERNS
            ):
                removed.append(sentence.strip())
                continue
            kept.append(sentence)
        out_lines.append(" ".join(kept) if kept else "")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines))


def excise_unattributed_authority(payload: dict[str, Any]) -> dict[str, Any]:
    """Post-revision backstop: excise sentences the writer didn't fix.
    Mutates and returns payload; removals recorded under
    payload['_authority_removed'] so the final_output stays auditable."""
    from app.core.config import AUTHORITY_GATE_ENABLED

    if not AUTHORITY_GATE_ENABLED:
        return payload
    body = payload.get("body")
    if not isinstance(body, str) or not body:
        return payload
    if not find_unattributed_authority(body):
        return payload
    removed: list[str] = []
    new_body = _excise_from_prose(body, removed)
    if removed:
        logger.warning(
            "authority gate excised %d unattributed-authority sentence(s): %s",
            len(removed),
            " | ".join(s[:90] for s in removed[:5]),
        )
        payload["body"] = new_body
        payload["_authority_removed"] = removed
    return payload
