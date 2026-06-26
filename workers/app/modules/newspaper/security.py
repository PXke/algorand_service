from __future__ import annotations

import re

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)


def sanitize_body(text: str) -> str:
    return _SCRIPT_RE.sub("", text).strip()
