"""Strip unsafe HTML from writer-emitted article bodies."""

from __future__ import annotations

import re

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_EVENT_HANDLER_RE = re.compile(r"\s+on\w+\s*=", re.IGNORECASE)


def sanitize_markdown_body(text: str) -> str:
    """Strip obvious XSS vectors from article bodies before storage/display."""
    cleaned = _SCRIPT_RE.sub("", text)
    cleaned = _EVENT_HANDLER_RE.sub(" ", cleaned)
    return cleaned.strip()
