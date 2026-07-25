"""Sanitize a composed article body before it's stored."""

from __future__ import annotations

import re

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)


def sanitize_body(text: str) -> str:
    """Strip `<script>` tags and surrounding whitespace from an article body."""
    return _SCRIPT_RE.sub("", text).strip()
