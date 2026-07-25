"""Canonical content-category values shared by the classifier and admin UI."""

from __future__ import annotations

CONTENT_CATEGORIES: tuple[str, ...] = (
    "service",
    "news",
    "tool",
    "payment",
    "nft",
    "governance",
    "generic",
)

# Common misspellings / plurals from older clients or model output.
_CATEGORY_ALIASES: dict[str, str] = {
    "tools": "tool",
    "services": "service",
    "payments": "payment",
    "nfts": "nft",
}

QUALITY_LEVELS: tuple[str, ...] = (
    "high",
    "medium",
    "low",
    "spam",
)


def normalize_content_category(value: str | None, *, default: str = "generic") -> str:
    """Map a machine-category label to the canonical taxonomy."""
    raw = (value or "").strip().lower().replace(" ", "_")
    if not raw:
        return default
    raw = _CATEGORY_ALIASES.get(raw, raw)
    if raw in CONTENT_CATEGORIES:
        return raw
    return default


def is_content_category(value: str | None) -> bool:
    """Return whether value normalizes to a known content category."""
    raw = (value or "").strip().lower().replace(" ", "_")
    if not raw:
        return False
    raw = _CATEGORY_ALIASES.get(raw, raw)
    return raw in CONTENT_CATEGORIES
