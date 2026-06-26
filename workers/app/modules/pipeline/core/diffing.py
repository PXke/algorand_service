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
    return "\n".join(lines[:max_lines])
