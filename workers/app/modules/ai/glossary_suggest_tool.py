"""Writer-proposed glossary terms.

The writer never publishes a definition directly -- suggest_glossary_term
only ever writes a `status='draft'` row (an admin reviews and publishes it
from the Glossary admin tab, same as any hand-entered term). This keeps the
model out of the trust boundary the same way abort_article and
mark_breaking_news do: it can flag, never decide.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

SUGGEST_GLOSSARY_TERM_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "suggest_glossary_term",
        "description": (
            "Propose a new glossary entry for a genuinely complex term you used in "
            "THIS article that a general reader would not already know (e.g. a "
            "protocol mechanism, a piece of jargon, an acronym) -- NOT a well-known "
            "word, a project/company name, or a term already covered by an "
            "auto-linked glossary tooltip you saw in your draft. This does not add "
            "the term or link it anywhere; it only queues it as a draft for an "
            "admin to review and publish. Call it at most a few times per article, "
            "only for terms that would genuinely help a reader."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "term": {
                    "type": "string",
                    "description": "the term itself, as it should read to a reader (e.g. 'Liquid staking')",
                },
                "definition": {
                    "type": "string",
                    "description": (
                        "a short, plain-language definition (1-3 sentences) -- an admin "
                        "will edit this before publishing, so a solid first draft is enough"
                    ),
                },
            },
            "required": ["term", "definition"],
        },
    },
}

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _slugify(term: str) -> str:
    slug = _SLUG_STRIP_RE.sub("-", term.strip().lower()).strip("-")
    return slug[:200]


def _slug_words(slug: str) -> set[str]:
    return {w for w in slug.split("-") if w}


def _find_near_duplicate_slug(
    new_slug: str, existing_slugs: list[str], *, min_overlap: float = 0.6
) -> str | None:
    """An existing slug that shares most of new_slug's distinguishing words (e.g. 'aid-trust-portal' vs 'aid-trust-portal-atp'), or None. Word-overlap, not edit-distance — a model rewording the SAME concept tends to keep the same core nouns and add/drop a qualifier, which this catches; unrelated terms that happen to share one common word don't (min_overlap is against the SMALLER set, so a short slug fully contained in a longer one still counts)."""
    new_words = _slug_words(new_slug)
    if not new_words:
        return None
    for existing in existing_slugs:
        if existing == new_slug:
            continue  # exact match — handled separately by the INSERT's IF NOT EXISTS
        existing_words = _slug_words(existing)
        smaller = min(len(new_words), len(existing_words))
        if smaller == 0:
            continue
        overlap = len(new_words & existing_words) / smaller
        if overlap >= min_overlap:
            return existing
    return None


def _make_suggest_glossary_term_handler(
    context: dict[str, Any] | None,
) -> Callable[..., dict[str, Any]]:
    ctx = context or {}

    def _handler(term: str = "", definition: str = "", **_: object) -> dict[str, Any]:
        term = (term or "").strip()[:200]
        definition = (definition or "").strip()[:2000]
        if not term or not definition:
            return {"ok": False, "error": "term and definition are both required"}
        slug = _slugify(term)
        if not slug:
            return {"ok": False, "error": "term did not produce a usable slug"}

        try:
            from datetime import UTC, datetime

            from app.core.cassandra import get_cassandra_session
            from app.core.statements import GlossaryStmts

            session = get_cassandra_session()

            # Near-duplicate check BEFORE inserting: the exact-slug IF NOT
            # EXISTS below only catches a verbatim repeat — a model
            # rewording the same concept ("Aid Trust Portal" then "Aid
            # Trust Portal (ATP)") produces a different slug and sails
            # through, piling up near-identical drafts for an admin to
            # de-dup by hand (root-caused 2026-08-06, alongside the same
            # session's excessive call-volume issue — a different problem
            # needing a different fix, since capping call COUNT doesn't
            # stop each of those calls from being a real near-duplicate).
            existing_slugs = [
                row.slug for row in session.execute(GlossaryStmts.LIST_ALL) if row.slug
            ]
            near_dup = _find_near_duplicate_slug(slug, existing_slugs)
            if near_dup is not None:
                return {
                    "ok": False,
                    "already_exists": True,
                    "slug": near_dup,
                    "hint": (
                        f"a very similar term already exists ('{near_dup}') — this looks "
                        "like the same concept reworded, not a new term; no need to "
                        "suggest it again"
                    ),
                }

            service_id = str(ctx.get("service_id", "")).strip()
            model = str(ctx.get("model", "")).strip()
            created_by = f"writer:{service_id or model or 'unknown'}"
            now = datetime.now(tz=UTC)
            result = session.execute(
                GlossaryStmts.INSERT_SUGGESTED,
                (slug, term, definition, [], now, now, created_by),
            )
            applied = bool(getattr(result, "was_applied", True))
        except Exception:
            return {"ok": False, "error": "could not record suggestion"}

        if not applied:
            return {
                "ok": False,
                "already_exists": True,
                "slug": slug,
                "hint": "this term is already in the glossary (or already suggested) -- no need to suggest it again",
            }
        return {"ok": True, "slug": slug, "noted": term}

    return _handler
