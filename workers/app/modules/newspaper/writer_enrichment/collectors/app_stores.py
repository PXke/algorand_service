from __future__ import annotations

import re
from typing import Any

_STORE_PATTERNS = (
    (re.compile(r"play\.google\.com/store/apps/details", re.I), "google_play"),
    (re.compile(r"apps\.apple\.com/.*/app/", re.I), "apple_app_store"),
    (re.compile(r"apps\.apple\.com/app/", re.I), "apple_app_store"),
)


def detect_app_store_links(page_text: str, source_url: str = "") -> dict[str, Any]:
    """Parse crawl text for store URLs — full store API search is a later phase."""
    blob = f"{source_url}\n{page_text}"
    found: list[str] = []
    for pattern, label in _STORE_PATTERNS:
        if pattern.search(blob) and label not in found:
            found.append(label)
    return {
        "stores_linked": found,
        "has_mobile_app_links": bool(found),
        "store_api_lookup": "not_implemented",
    }
