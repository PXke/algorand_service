"""Reader-facing tag policy, loaded from ``shared/taxonomy.json``.

One list of provenance labels, used by the Python SSR and — via the generated
``frontend/src/lib/taxonomy.generated.ts`` — by the SPA, so a story's kicker is
the same before and after hydration.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "taxonomy.json"


@lru_cache(maxsize=1)
def _data() -> dict[str, object]:
    return json.loads(_TAXONOMY_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def meta_tags() -> frozenset[str]:
    """Pipeline/provenance labels that must never become a kicker."""
    return frozenset(str(t).strip().lower() for t in _data()["meta_tags"])  # type: ignore[union-attr]


@lru_cache(maxsize=1)
def _display_labels() -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in _data()["display_labels"].items()}  # type: ignore[union-attr]


def is_meta_tag(tag: str) -> bool:
    """Whether a tag says how we found the story rather than what it's about."""
    return tag.strip().lower() in meta_tags()


def display_tag_label(tag: str) -> str:
    """Map an internal tag slug to its reader-facing label.

    Explicit ``display_labels`` win. Unmapped slugs are opened into a
    sentence-style label so a desk directory never prints raw CMS slugs.
    """
    key = tag.strip().lower()
    if not key:
        return tag
    mapped = _display_labels().get(key)
    if mapped is not None:
        return mapped
    opened = key.replace("-", " ").replace("_", " ")
    return opened[:1].upper() + opened[1:] if opened else tag


def display_tag_title(tag: str) -> str:
    """Reader-facing label for headings and <title>."""
    return display_tag_label(tag)


def primary_tag(tags: list[str] | None) -> str | None:
    """First topical tag (raw slug), falling back to the plain first tag.

    Raw, not display text: ``/topic/<tag>`` URLs are built from this, and
    ``display_tag_label`` is applied separately to the visible string.
    """
    cleaned = [t.strip().lower() for t in (tags or []) if t.strip()]
    for tag in cleaned:
        if tag not in meta_tags():
            return tag
    return cleaned[0] if cleaned else None


def order_reader_tags(tags: list[str] | None) -> list[str]:
    """Topical tags first, provenance chips last, deduped and lowercased."""
    topical: list[str] = []
    meta: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        tag = str(raw).strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        (meta if tag in meta_tags() else topical).append(tag)
    return [*topical, *meta]
