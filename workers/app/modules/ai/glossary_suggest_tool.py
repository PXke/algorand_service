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

            service_id = str(ctx.get("service_id", "")).strip()
            model = str(ctx.get("model", "")).strip()
            created_by = f"writer:{service_id or model or 'unknown'}"
            now = datetime.now(tz=UTC)
            result = get_cassandra_session().execute(
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
