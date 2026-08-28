"""Translate a glossary term's own-language term+definition via the translate-tier LLM.

Short, unstructured text (a name and 1-3 sentences) -- unlike article bodies,
there's no markdown structure to block-align, so this is a plain JSON-object
call rather than translate_article's block-aligned machinery.
"""

from __future__ import annotations

import logging
import time

from app.modules.ai.llm_openai_compatible import MistralProvider
from app.modules.ai.llm_purpose_router import get_llm_translate_client

logger = logging.getLogger(__name__)


def _record_glossary_translate_session(
    llm: MistralProvider, *, term: str, target_language: str, status: str, duration_ms: int
) -> None:
    """Best-effort compose_sessions row for one glossary-term translation call.

    This runs entirely OUTSIDE any article compose session -- triggered by an
    admin publishing/editing a glossary term, on its own ephemeral translate-
    tier client -- so no article's compose_sessions row (or its
    research_llm/llm usage sum) ever accounts for this spend. Reuses the
    SAME accounting mechanism the compose path already writes to
    (SessionRegisterCassandra -> compose_sessions), rather than inventing a
    second one, with a `glossary_translate:` service_id prefix so an admin
    reading the Sessions tab can tell it apart from an article compose
    (2026-08-28 audit).

    Never raises: a failure here must never surface as (or be mistaken for) a
    translation failure -- see translate_glossary_term_task's own fail-open
    docstring.
    """
    try:
        from app.modules.ai.session_register import SessionRegisterCassandra

        register = SessionRegisterCassandra()
        session_id, created_at = register.new_ref()
        usage = llm.usage_totals()
        register.upsert(
            debug={"messages": []},
            trace=[],
            service_id=f"glossary_translate:{term[:200]}",
            source_url="",
            model=llm.model,
            final_output="",
            status=status,
            duration_ms=duration_ms,
            session_id=session_id,
            created_at=created_at,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
            cached_tokens=usage["cached_tokens"],
        )
    except Exception:
        logger.warning(
            "failed to record glossary-translate session for %r/%s",
            term,
            target_language,
            exc_info=True,
        )


def translate_glossary_term(
    *,
    term: str,
    definition: str,
    target_language: str,
    client: MistralProvider | None = None,
) -> dict[str, str]:
    """Translate a glossary term+definition pair to the target language."""
    from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANG_NAMES

    llm = client or get_llm_translate_client()
    lang_name = ARTICLE_TRANSLATION_LANG_NAMES.get(target_language, target_language)

    system = (
        f"You translate a glossary entry (a short technical term and its plain-language "
        f"definition) into natural, idiomatic {lang_name} for a native reader. Do not "
        "transliterate or calque English phrasing. Do not translate product/protocol/brand "
        "names (e.g. 'Algorand', 'ALGO') -- keep them as-is. Preserve the meaning exactly."
    )
    user = (
        f"Term: {term}\nDefinition: {definition}\n\n"
        'Respond as JSON: {"term": "...", "definition": "..."}'
    )
    t0 = time.monotonic()
    status = "ok"
    try:
        parsed = llm.chat_json_object(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=400,
        )
        translated_term = str(parsed.get("term", "")).strip() or term
        translated_definition = str(parsed.get("definition", "")).strip() or definition
        return {"term": translated_term, "definition": translated_definition}
    except Exception:
        status = "error"
        raise
    finally:
        _record_glossary_translate_session(
            llm,
            term=term,
            target_language=target_language,
            status=status,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
