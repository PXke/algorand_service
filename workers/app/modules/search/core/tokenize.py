"""Index-time search token extraction for Typesense.

Typesense lowercases and splits on whitespace/punctuation. Short acronyms
(US, USA) and dotted forms (U.S.) become separate tokens, but a query for
"USA" still won't hit "US" unless we expand at index time (or use synonyms).
This module builds a `tokens` field: tags, detected acronyms, and synonym-
cluster members when any cluster term appears as a whole word.
"""

from __future__ import annotations

import re

# Keep synonym clusters in sync with backend/app/core/typesense_client.py
SEARCH_TOKEN_CLUSTERS: tuple[tuple[str, ...], ...] = (
    ("usa", "us", "u.s.", "u.s.a.", "united states"),
    ("uk", "u.k.", "united kingdom"),
    ("eu", "e.u.", "european union"),
)

_ALL_CAPS_RE = re.compile(r"\b[A-Z]{2,5}\b")
_DOTTED_ACRONYM_RE = re.compile(r"\b[A-Z](?:\.[A-Z])+\.?\b")


def _word_present(term: str, text: str) -> bool:
    escaped = re.escape(term.lower())
    return re.search(rf"(?<![\w]){escaped}(?![\w])", text, re.IGNORECASE | re.UNICODE) is not None


def _normalize_dotted_acronym(value: str) -> str:
    return re.sub(r"\.", "", value).lower()


def build_article_search_tokens(
    *,
    title: str,
    summary: str,
    body: str,
    tags: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    tokens: set[str] = set()
    for raw in tags or ():
        tag = raw.strip().lower()
        if tag:
            tokens.add(tag)

    text = f"{title}\n{summary}\n{body}"
    lower = text.lower()

    for match in _ALL_CAPS_RE.finditer(text):
        tokens.add(match.group().lower())

    for match in _DOTTED_ACRONYM_RE.finditer(text):
        tokens.add(_normalize_dotted_acronym(match.group()))

    for cluster in SEARCH_TOKEN_CLUSTERS:
        if any(_word_present(term, lower) for term in cluster):
            tokens.update(cluster)

    return sorted(t for t in tokens if t)
