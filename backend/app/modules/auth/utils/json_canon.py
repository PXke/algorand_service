"""RFC 8785-style canonical JSON encoding for signing."""

from __future__ import annotations

import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:  # noqa: ANN401 -- any JSON-serializable value
    """RFC 8785-style canonical JSON (sorted keys, minimal separators)."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
