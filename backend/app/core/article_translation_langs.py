"""Article publish-time translation targets (Mistral Small tier).

The actual language list/names live in `algorand_shared.translation_langs`,
shared with workers/app/core/article_translation_langs.py.
"""

from __future__ import annotations

from algorand_shared.translation_langs import (
    ARTICLE_TRANSLATION_LANG_NAMES as ARTICLE_TRANSLATION_LANG_NAMES,
)
from algorand_shared.translation_langs import (
    ARTICLE_TRANSLATION_LANGS as ARTICLE_TRANSLATION_LANGS,
)

# BCP-47 tags for hreflang / <html lang>.
SEO_HREFLANG_LOCALES: dict[str, str] = {
    "en": "en",
    "fa": "fa",
    "ps": "ps",
    "ar": "ar",
    "ru": "ru",
    "zh": "zh-Hans",
    "hi": "hi",
    "es": "es",
    "fr": "fr",
}

SEO_OG_LOCALES: dict[str, str] = {
    "en": "en_US",
    "fa": "fa_IR",
    "ps": "ps_AF",
    "ar": "ar",
    "ru": "ru_RU",
    "zh": "zh_CN",
    "hi": "hi_IN",
    "es": "es_ES",
    "fr": "fr_FR",
}


def html_lang_for(lang: str | None) -> str:
    """Return the BCP-47 tag for `<html lang>`, defaulting to English."""
    code = (lang or "en").strip() or "en"
    return SEO_HREFLANG_LOCALES.get(code, code)


def og_locale_for(lang: str | None) -> str:
    """Return the Open Graph locale code, defaulting to en_US."""
    code = (lang or "en").strip() or "en"
    return SEO_OG_LOCALES.get(code, "en_US")
