"""Deterministic post-compose glossary auto-linking.

No model involvement: the writer never decides what's in the glossary or
when to link it. Known terms come from the admin-curated glossary (backend
app.modules.glossary), and this module does pure structural string matching
against a composed article's body -- same "structural, not semantic" posture
as ingest_signal.py's live-activity-block stripper.

The FIRST occurrence of each published term (or one of its aliases) gets a
markdown link with a native title-attribute tooltip:
`[matched text](/glossary/slug "first 150 chars of the definition")`.
Later occurrences of the same term are left alone -- repeating the same
definition link throughout an article is noise, not help. Code fences and
headings are skipped, and an already-linked span (whether from a prior
auto-link pass on a recompose, or the writer's own markdown link) is never
re-linked or overlapped.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from app.modules.ai.llm_compose import split_markdown_blocks

# Small, rarely-changing admin table -- cache the published set in-process
# rather than re-scanning Cassandra on every single article write.
_CACHE_TTL_SECONDS = 300
_cache: dict[str, object] = {"at": 0.0, "terms": ()}

_EXISTING_LINK_SPAN_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
_EXISTING_GLOSSARY_SLUG_RE = re.compile(r"\]\(/glossary/([a-z0-9-]+)[\s)]")


@dataclass(frozen=True)
class GlossaryLinkTerm:
    """One published glossary entry's linkable phrases."""

    slug: str
    term: str
    definition: str
    aliases: tuple[str, ...] = ()


def _fetch_published_terms() -> tuple[GlossaryLinkTerm, ...]:
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import GlossaryStmts

    try:
        rows = get_cassandra_session().execute(GlossaryStmts.LIST_ALL)
    except Exception:
        return ()
    out = []
    for r in rows:
        if str(getattr(r, "status", "") or "") != "published":
            continue
        term = str(getattr(r, "term", "") or "").strip()
        slug = str(getattr(r, "slug", "") or "").strip()
        if not term or not slug:
            continue
        out.append(
            GlossaryLinkTerm(
                slug=slug,
                term=term,
                definition=str(getattr(r, "definition", "") or ""),
                aliases=tuple(a.strip() for a in (getattr(r, "aliases", None) or ()) if a.strip()),
            )
        )
    return tuple(out)


def published_terms_cached() -> tuple[GlossaryLinkTerm, ...]:
    """Published glossary terms, refetched at most once per _CACHE_TTL_SECONDS."""
    now = time.monotonic()
    if now - float(_cache["at"]) > _CACHE_TTL_SECONDS:
        _cache["terms"] = _fetch_published_terms()
        _cache["at"] = now
    return _cache["terms"]  # type: ignore[return-value]


def _phrase_entries(terms: tuple[GlossaryLinkTerm, ...]) -> list[tuple[str, str, str]]:
    """(phrase, slug, definition) for every term + alias, longest phrase first so a multi-word term wins over a shorter one that happens to be its substring."""
    entries: list[tuple[str, str, str]] = [
        (phrase, t.slug, t.definition)
        for t in terms
        for phrase in (t.term, *t.aliases)
        if phrase
    ]
    entries.sort(key=lambda e: -len(e[0]))
    return entries


def _already_linked_slugs(body: str) -> set[str]:
    """Slugs already linked anywhere in the body -- seeds the skip-set so a recompose doesn't stack a second link for a term the previous pass already placed."""
    return set(_EXISTING_GLOSSARY_SLUG_RE.findall(body))


def _link_block(block: str, entries: list[tuple[str, str, str]], linked_slugs: set[str]) -> str:
    """Link the first (in TEXT ORDER, not phrase-list order) unclaimed match in this block.

    A single combined alternation, scanned left-to-right via one finditer
    pass, rather than looping phrases outer/text inner -- looping phrases
    first would let a long candidate phrase "win" a later position over a
    short alias that genuinely appears earlier in the text. Phrases are
    still listed longest-first WITHIN the alternation, so two candidates
    starting at the exact same position (a real substring overlap, e.g.
    "staking" inside "liquid staking") still resolve to the more specific
    term -- Python's re tries alternatives in the given order at each
    position, first match wins.
    """
    if block.lstrip().startswith(("```", "#")):
        return block
    candidates = [(p, s, d) for p, s, d in entries if s not in linked_slugs]
    if not candidates:
        return block

    group_map: dict[str, tuple[str, str]] = {}
    parts = []
    for i, (phrase, slug, definition) in enumerate(candidates):
        name = f"g{i}"
        group_map[name] = (slug, definition)
        parts.append(f"(?P<{name}>\\b{re.escape(phrase)}\\b)")
    pattern = re.compile("|".join(parts), re.IGNORECASE)
    existing_spans = [m.span() for m in _EXISTING_LINK_SPAN_RE.finditer(block)]

    out: list[str] = []
    last_end = 0
    for m in pattern.finditer(block):
        slug, definition = group_map[m.lastgroup or ""]
        if slug in linked_slugs:
            continue  # a different phrase for this slug already claimed it earlier in this block
        if any(s <= m.start() < e or s < m.end() <= e for s, e in existing_spans):
            continue
        out.append(block[last_end : m.start()])
        matched_text = block[m.start() : m.end()]
        title = definition.replace('"', "'").replace("\n", " ").strip()[:150]
        replacement = (
            f'[{matched_text}](/glossary/{slug} "{title}")'
            if title
            else f"[{matched_text}](/glossary/{slug})"
        )
        out.append(replacement)
        last_end = m.end()
        linked_slugs.add(slug)
    out.append(block[last_end:])
    return "".join(out)


def auto_link_glossary_terms(body: str, terms: tuple[GlossaryLinkTerm, ...] | None = None) -> str:
    """Insert a glossary link on the first occurrence of each published term/alias found in `body`. Best-effort: never raises, returns `body` unchanged on any error (an article must never fail to save over a linking bug)."""
    if not body:
        return body
    try:
        terms = terms if terms is not None else published_terms_cached()
        if not terms:
            return body
        entries = _phrase_entries(terms)
        linked_slugs = _already_linked_slugs(body)
        blocks = split_markdown_blocks(body)
        linked = [_link_block(b, entries, linked_slugs) for b in blocks]
        return "\n\n".join(linked).strip() or body
    except Exception:
        return body
