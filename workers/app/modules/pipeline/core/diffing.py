"""Whitespace-normalized unified diff between two snapshot texts."""

from __future__ import annotations

import re
from difflib import unified_diff

_WHITESPACE_RE = re.compile(r"[ \t]+")


def normalize_text(text: str) -> str:
    """Collapse horizontal whitespace and trim lines to reduce diff noise."""
    lines = []
    for line in text.splitlines():
        collapsed = _WHITESPACE_RE.sub(" ", line).strip()
        lines.append(collapsed)
    return "\n".join(lines)


def build_text_diff(previous: str, current: str, max_lines: int = 200) -> str:
    """Return a whitespace-normalized unified diff, truncated to max_lines with a marker."""
    prev_norm = normalize_text(previous)
    curr_norm = normalize_text(current)
    diff = unified_diff(
        prev_norm.splitlines(),
        curr_norm.splitlines(),
        fromfile="previous",
        tofile="current",
        lineterm="",
    )
    lines = list(diff)
    truncated = lines[:max_lines]
    # difflib's hunk headers (e.g. "@@ -2,12 +2,1181 @@") reflect the FULL
    # diff's true size even after we cut it down below — without this marker
    # a consumer (writer prompt or human) sees a header claiming far more
    # lines changed than are actually shown, with no indication anything was
    # cut (root-caused an overstated "massive expansion" framing on an
    # AlgoSeas article, 2026-07-13/14).
    omitted = len(lines) - len(truncated)
    if omitted > 0:
        truncated.append(f"... ({omitted} more diff lines omitted)")
    return "\n".join(truncated)
