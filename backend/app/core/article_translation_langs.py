"""Article publish-time translation targets (Mistral Small tier).

Keep in sync with workers/app/core/article_translation_langs.py
"""

from __future__ import annotations

# Corridor-first: HesabPay / UNDP regions (Afghanistan, Syria), then global.
#
# `fa` targets STANDARD PERSIAN (Farsi), not Afghan Dari, as of 2026-07-29.
# The two are mutually intelligible — the literature puts the gap at roughly
# European vs Canadian French — so Afghan readers lose nothing, while the
# audience widens from Afghanistan alone to the whole Persian continuum
# (Iran, Afghanistan, Tajik diaspora). Farsi is also far better resourced in
# machine translation than Dari, so the output quality improves for free.
# The hreflang is unqualified `fa` for the same reason: `fa-AF` volunteered a
# regional restriction we do not want.
ARTICLE_TRANSLATION_LANGS: tuple[str, ...] = (
    "fa",  # Persian/Farsi — Iran + Afghanistan (HesabPay corridor)
    "ps",  # Pashto — Afghanistan (HesabPay)
    "ar",  # Arabic — Syria, Sudan
    "ru",  # Russian — Central Asia diaspora
    "zh",
    "hi",
    "es",
    "fr",
)

ARTICLE_TRANSLATION_LANG_NAMES: dict[str, str] = {
    "fa": "Persian (Farsi)",
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
