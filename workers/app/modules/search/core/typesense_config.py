"""Shared Typesense client construction and configuration check."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typesense


def typesense_api_key() -> str:
    """Return the configured Typesense API key, or "" if unset."""
    return os.getenv("TYPESENSE_API_KEY", "").strip()


def is_typesense_configured() -> bool:
    """Return whether a Typesense API key is configured."""
    return bool(typesense_api_key())


def build_typesense_client() -> typesense.Client | None:
    """Build a Typesense client from env config, or None if unconfigured/unavailable."""
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
