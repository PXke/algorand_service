"""Post-hoc glossary-term extraction over the finished article body.

suggest_glossary_term is only offered as a tool during Stage 1 (research),
before the article's prose exists -- despite its own instructions asking for
"a term you used in THIS article." Confirmed empirically 2026-08-03: 0 of 62
real compose sessions ever called it, and glossary_terms had 0 rows total.

This runs as a deterministic post-compose step instead (same shape as the
other *_gate.py modules): one small classification call over the FINISHED
body, so the model judges real prose instead of guessing ahead of it. Draft
rows only -- an admin still reviews and publishes from the Glossary tab,
same trust boundary as the tool it replaces.

Fail-open throughout: any error here must never affect the published
article, only skip queuing suggestions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.ai.mistral_client import MistralClient

logger = logging.getLogger(__name__)

_MAX_TERMS = 3

_SYSTEM = (
    "You extract genuinely complex terms from a finished news article that a "
    "general reader would not already know: a protocol mechanism, jargon, an "
    "acronym. Do NOT include well-known words, project/company names, or any "
    "term you are not confident is genuinely unfamiliar to a general reader. "
    f"Return at most {_MAX_TERMS} terms -- fewer, or zero, is fine and expected "
    "for most articles."
)


def _extract_terms(title: str, body: str, client: MistralClient) -> list[dict[str, str]]:
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Title: {title}\n\nArticle body:\n{body[:8000]}\n\n"
                'Respond as JSON: {"terms": [{"term": "...", '
                '"definition": "1-3 plain-language sentences"}]}. '
                'Use {"terms": []} if nothing qualifies.'
            ),
        },
    ]
    parsed = client.chat_json_object(messages, temperature=0.0, max_tokens=600)
    terms = parsed.get("terms") if isinstance(parsed, dict) else None
    out: list[dict[str, str]] = []
    for t in list(terms or [])[:_MAX_TERMS]:
        if not isinstance(t, dict):
            continue
        term = str(t.get("term", "")).strip()
        definition = str(t.get("definition", "")).strip()
        if term and definition:
            out.append({"term": term, "definition": definition})
    return out


def suggest_glossary_terms(
    payload: dict[str, Any], *, client: MistralClient, service_id: str = ""
) -> dict[str, Any]:
    """Best-effort: queue draft glossary suggestions for genuinely complex terms in the finished body.

    Takes an already-resolved client rather than fetching its own — same
    reason _grade_current_draft takes `quality_mistral` as a parameter
    instead of importing a client internally: callers (and their tests) mock
    the client they already hold, not a second one a callee reaches for on
    its own. Side-effect only -- never mutates payload's article fields, so a
    failure here can never change what gets published.
    """
    from app.core.config import GLOSSARY_SUGGEST_GATE_ENABLED

    if not GLOSSARY_SUGGEST_GATE_ENABLED:
        return payload
    body = payload.get("body")
    title = payload.get("title")
    if not isinstance(body, str) or not body or not isinstance(title, str) or not title:
        return payload
    try:
        from app.modules.ai.glossary_suggest_tool import _make_suggest_glossary_term_handler

        terms = _extract_terms(title, body, client)
        if not terms:
            return payload
        handler = _make_suggest_glossary_term_handler(
            {"service_id": service_id, "model": "glossary_suggest_gate"}
        )
        for t in terms:
            result = handler(term=t["term"], definition=t["definition"])
            if result.get("ok"):
                logger.info("glossary-suggest gate queued draft term %r", t["term"])
    except Exception:
        logger.warning("glossary-suggest gate failed (fail-open)", exc_info=True)
    return payload
