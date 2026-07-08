"""Article publish-time translation targets (Mistral Small tier).

Keep in sync with backend/app/core/article_translation_langs.py
"""

from __future__ import annotations

ARTICLE_TRANSLATION_LANGS: tuple[str, ...] = (
    "fa",
    "ps",
    "ar",
    "ru",
    "zh",
    "hi",
    "es",
    "fr",
)

ARTICLE_TRANSLATION_LANG_NAMES: dict[str, str] = {
    "fa": "Dari (Afghan Persian)",
    "ps": "Pashto",
    "ar": "Arabic",
    "ru": "Russian",
    "zh": "Chinese (Simplified)",
    "hi": "Hindi",
    "es": "Spanish (Castilian)",
    "fr": "French",
}
