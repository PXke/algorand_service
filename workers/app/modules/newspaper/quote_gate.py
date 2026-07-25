"""Deterministic quotation-integrity gate for composed article bodies.

Root-caused 2026-07-16 (RandGallery shutdown article): the writer attributed
an invented phrase to the Goanna Council in quotation marks — "stack of
legacy systems layered on top of one another" appeared nowhere in the
research trace or the supplied announcement. Quotation marks are a verbatim-
transcription claim; a newspaper must never print invented words as a quote.

The rule: any quoted span of QUOTE_MIN_WORDS+ words must appear (normalized:
case, punctuation and curly/straight quote folding) somewhere in the ground
corpus — the research trace plus the compose input (source text / editorial
brief). An ungrounded quote is DE-QUOTED: the words survive as paraphrase,
only the verbatim claim (the quotation marks) is dropped. Short quoted
fragments (1-3 words: scare quotes, product names) are left alone — too many
legitimate uses, too little verbatim claim.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

QUOTE_MIN_WORDS = 4
_MAX_QUOTE_CHARS = 400  # unbalanced straight quotes must not eat paragraphs

# Curly first (unambiguous), then straight double quotes.
_QUOTE_SPAN_RE = re.compile(
    rf"[“]([^”]{{1,{_MAX_QUOTE_CHARS}}})[”]|\"([^\"]{{1,{_MAX_QUOTE_CHARS}}})\""
)

_FOLD_RE = re.compile(r"[^a-z0-9]+")


def _fold(text: str) -> str:
    """Case/punctuation-insensitive form for verbatim comparison."""
    return _FOLD_RE.sub(" ", (text or "").lower()).strip()


def _ground_corpus(trace: list[dict] | None, extra_texts: list[str]) -> str:
    parts: list[str] = []
    for entry in trace or ():
        try:
            parts.append(json.dumps(entry))
        except (TypeError, ValueError):
            parts.append(str(entry))
    parts.extend(t for t in extra_texts if t)
    return _fold(" ".join(parts))


def unquote_ungrounded_quotes(
    payload: dict[str, Any],
    trace: list[dict] | None,
    *,
    extra_texts: list[str] | None = None,
) -> dict[str, Any]:
    """De-quote body quotations that aren't verbatim in the ground corpus.

    Mutates and returns payload; records removals under
    payload['_quotes_unquoted'] so the persisted final_output stays auditable.
    """
    from app.core.config import QUOTE_GATE_ENABLED

    if not QUOTE_GATE_ENABLED:
        return payload
    body = payload.get("body")
    if not isinstance(body, str) or not body:
        return payload
    corpus = _ground_corpus(trace, list(extra_texts or []))
    removed: list[str] = []

    def _replace(match: re.Match) -> str:
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        folded = _fold(inner)
        if len(folded.split()) < QUOTE_MIN_WORDS:
            return match.group(0)  # scare quotes / names — no verbatim claim
        if folded and folded in corpus:
            return match.group(0)  # genuinely verbatim — keep the quote
        removed.append(inner)
        return inner  # drop only the quotation marks; the words survive

    new_body = _QUOTE_SPAN_RE.sub(_replace, body)
    if removed:
        logger.warning(
            "quote gate de-quoted %d ungrounded quotation(s): %s",
            len(removed),
            " | ".join(q[:80] for q in removed[:5]),
        )
        payload["body"] = new_body
        payload["_quotes_unquoted"] = removed
    return payload
