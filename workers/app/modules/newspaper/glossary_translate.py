"""Translate a glossary term's own-language term+definition via the translate-tier LLM.

Short, unstructured text (a name and 1-3 sentences) -- unlike article bodies,
there's no markdown structure to block-align, so this is a plain JSON-object
call rather than translate_article's block-aligned machinery.
"""

from __future__ import annotations

from app.modules.ai.llm_openai_compatible import MistralProvider
from app.modules.ai.llm_purpose_router import get_llm_translate_client


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
    parsed = llm.chat_json_object(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=400,
    )
    translated_term = str(parsed.get("term", "")).strip() or term
    translated_definition = str(parsed.get("definition", "")).strip() or definition
    return {"term": translated_term, "definition": translated_definition}
