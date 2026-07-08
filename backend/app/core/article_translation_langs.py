"""Article publish-time translation targets (Mistral Small tier).

Keep in sync with workers/app/core/article_translation_langs.py
"""

from __future__ import annotations

# Corridor-first: HesabPay / UNDP regions (Afghanistan, Syria), then global.
ARTICLE_TRANSLATION_LANGS: tuple[str, ...] = (
    "fa",  # Dari — Afghanistan (HesabPay)
    "ps",  # Pashto — Afghanistan (HesabPay)
    "ar",  # Arabic — Syria, Sudan
    "ru",  # Russian — Central Asia diaspora
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

# BCP-47 tags for hreflang / <html lang>.
SEO_HREFLANG_LOCALES: dict[str, str] = {
    "en": "en",
    "fa": "fa-AF",
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
    "fa": "fa_AF",
    "ps": "ps_AF",
    "ar": "ar",
    "ru": "ru_RU",
    "zh": "zh_CN",
    "hi": "hi_IN",
    "es": "es_ES",
    "fr": "fr_FR",
}


def html_lang_for(lang: str | None) -> str:
    code = (lang or "en").strip() or "en"
    return SEO_HREFLANG_LOCALES.get(code, code)


def og_locale_for(lang: str | None) -> str:
    code = (lang or "en").strip() or "en"
    return SEO_OG_LOCALES.get(code, "en_US")
