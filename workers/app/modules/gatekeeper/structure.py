"""Deterministic structural-quality heuristics for Markdown news articles.

Pure ``re`` + string parsing (no markdown library). Each heuristic is an isolated,
testable helper; ``structure_report_markdown`` formats them into a table and
``evaluate_structure`` returns the same data as a dict for the pipeline.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# --- thresholds -----------------------------------------------------------
MAX_DESERT_PARAGRAPHS = 4  # FAIL when > 4 consecutive prose blocks
MAX_METRICS_PER_PARAGRAPH = 3  # FAIL when > 3 distinct metrics in one block
MIN_LINKS_PER_100_WORDS = 1.0  # FAIL when < 1.0

# Blockchain metrics that belong in a table once they pile up in prose.
_METRIC_RES = (
    re.compile(r"\d[\d,]*(?:\.\d+)?\s*TPS", re.I),
    re.compile(r"\d+(?:\.\d+)?\s*(?:ms|milliseconds)\b", re.I),
    re.compile(r"\d[\d,]*(?:\.\d+)?\s*ALGO\b", re.I),
    re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:[KMB]|thousand|million|billion|trillion)?", re.I),
    re.compile(r"\d+(?:\.\d+)?\s*%"),
    re.compile(r"\d+(?:\.\d+)?\s*(?:million|billion|trillion)\b", re.I),
)

_HEADING_RE = re.compile(r"^(#{1,6})\s")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]*-{2,}[\s:|-]*$")
_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\([^)]+\)")


# --- shared parsing helpers ----------------------------------------------
def _strip_code(md: str) -> str:
    """Markdown with fenced and inline code removed (for word/link counting)."""
    md = re.sub(r"```.*?```", " ", md, flags=re.S)
    return re.sub(r"`[^`]*`", " ", md)


def _segments(md: str) -> Iterator[tuple[str, str]]:
    """Yield (kind, text) splitting fenced code from prose so a code block is one opaque unit (its blank lines must not be mistaken for paragraph breaks)."""
    for part in re.split(r"(```.*?```)", md, flags=re.S):
        if part.startswith("```"):
            yield "code", part
        elif part.strip():
            yield "text", part


def _block_type(block: str) -> str:
    lines = block.splitlines()
    if _HEADING_RE.match(lines[0]):
        return "heading"
    if any(_TABLE_SEP_RE.match(ln) for ln in lines) or sum("|" in ln for ln in lines) >= 2:
        return "table"
    if _LIST_RE.match(lines[0]):
        return "list"
    return "paragraph"


def _classify_blocks(md: str) -> list[tuple[str, str]]:
    """Ordered (type, text) blocks. type in heading|table|list|code|paragraph."""
    blocks: list[tuple[str, str]] = []
    for kind, seg in _segments(md):
        if kind == "code":
            blocks.append(("code", seg))
            continue
        for raw in re.split(r"\n\s*\n", seg):
            b = raw.strip()
            if b:
                blocks.append((_block_type(b), b))
    return blocks


def _distinct_metrics(text: str) -> list[str]:
    """Distinct metric mentions in ``text``, merging overlapping matches so "$5 million" counts once, not twice."""
    spans: list[tuple[int, int, str]] = []
    for rx in _METRIC_RES:
        spans.extend((m.start(), m.end(), m.group(0)) for m in rx.finditer(text))
    spans.sort()
    merged: list[list] = []
    for s, e, t in spans:
        if merged and s < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e, t])
    return [m[2].strip() for m in merged]


# --- heuristics -----------------------------------------------------------
@dataclass(frozen=True)
class Heuristic:
    """One structural-quality heuristic's observed value vs. threshold."""
    name: str
    observed: str
    threshold: str
    passed: bool


def formatting_deserts(blocks: list[tuple[str, str]]) -> Heuristic:
    """Longest run of consecutive prose blocks. A heading/table (and any other structural block: list/code) resets the run — those are formatting relief."""
    run = longest = 0
    for btype, _ in blocks:
        if btype == "paragraph":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return Heuristic(
        "Formatting Deserts",
        f"{longest} consecutive paragraphs",
        f"> {MAX_DESERT_PARAGRAPHS}",
        longest <= MAX_DESERT_PARAGRAPHS,
    )


def buried_metrics(blocks: list[tuple[str, str]]) -> Heuristic:
    """Most distinct metrics crammed into a single prose block (should be a table)."""
    worst = 0
    for btype, text in blocks:
        if btype == "paragraph":
            worst = max(worst, len(_distinct_metrics(text)))
    return Heuristic(
        "Buried Metrics",
        f"{worst} metrics in one paragraph",
        f"> {MAX_METRICS_PER_PARAGRAPH}",
        worst <= MAX_METRICS_PER_PARAGRAPH,
    )


def hierarchy_integrity(md: str) -> Heuristic:
    """Heading levels must not deepen by more than one at a time (## -> #### is a skipped level)."""
    from itertools import pairwise

    levels = [
        len(m.group(1)) for ln in _strip_code(md).splitlines() if (m := _HEADING_RE.match(ln))
    ]
    worst_jump = max((cur - prev for prev, cur in pairwise(levels)), default=0)
    observed = "no skips" if worst_jump <= 1 else f"jump of +{worst_jump} levels"
    return Heuristic("Hierarchy Integrity", observed, "jump > 1 level", worst_jump <= 1)


def citation_density(md: str) -> Heuristic:
    """Markdown links per 100 words (code blocks and images excluded)."""
    text = _strip_code(md)
    links = len(_LINK_RE.findall(text))
    words = len(re.findall(r"\b[\w'’]+\b", text))
    density = (links / words * 100) if words else 0.0
    return Heuristic(
        "Citation Density",
        f"{density:.2f} links / 100 words ({links} links, {words} words)",
        f"< {MIN_LINKS_PER_100_WORDS:.1f}",
        density >= MIN_LINKS_PER_100_WORDS,
    )


def evaluate_structure(md: str) -> list[Heuristic]:
    """Run all deterministic structure heuristics against a markdown body."""
    blocks = _classify_blocks(md)
    return [
        formatting_deserts(blocks),
        buried_metrics(blocks),
        hierarchy_integrity(md),
        citation_density(md),
    ]


def structure_score(md: str) -> float:
    """Fraction of structure heuristics that pass (0..1) — the deterministic structure component of the cold-start grade."""
    hs = evaluate_structure(md)
    return sum(h.passed for h in hs) / len(hs) if hs else 1.0


def structure_issues(md: str) -> list[str]:
    """Reviewer-facing messages for each failing structure heuristic."""
    return [f"structure — {h.name}: {h.observed}" for h in evaluate_structure(md) if not h.passed]


def structure_report_markdown(md: str) -> str:
    """Markdown table summarizing every heuristic with a PASS/FAIL status."""
    rows = ["| Heuristic | Observed | Threshold (fail) | Status |", "| :-- | :-- | :-- | :-- |"]
    rows.extend(
        f"| {h.name} | {h.observed} | {h.threshold} | {'✅ PASS' if h.passed else '❌ FAIL'} |"
        for h in evaluate_structure(md)
    )
    return "\n".join(rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sample = """# Algorand Q2 Network Report

## Throughput

Algorand sustained strong performance this quarter. The protocol is fast.
The team continued shipping. Adoption grew across the ecosystem. Developers
remain active. Community sentiment stayed positive through the period.

Validators reported the network handled 9400 TPS with 2.9 ms block latency
while TVL climbed to $312 million and staking rewards paid out 84,000 ALGO.

#### Governance

See the [governance portal](https://algorand.foundation) for details.
"""
    logger.info(structure_report_markdown(sample))
