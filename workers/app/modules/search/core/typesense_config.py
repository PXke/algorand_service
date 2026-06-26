from __future__ import annotations

import os
from typing import Any


def typesense_api_key() -> str:
    return os.getenv("TYPESENSE_API_KEY", "").strip()


def is_typesense_configured() -> bool:
    return bool(typesense_api_key())


def build_typesense_client() -> Any | None:
    if not is_typesense_configured():
        return None
    try:
        import typesense
    except ImportError:
        return None

    host = os.getenv("TYPESENSE_HOST", "localhost")
    port = os.getenv("TYPESENSE_PORT", "8108")
    protocol = os.getenv("TYPESENSE_PROTOCOL", "http")
    return typesense.Client(
        {
            "nodes": [{"host": host, "port": port, "protocol": protocol}],
            "api_key": typesense_api_key(),
            "connection_timeout_seconds": 5,
        }
    )
