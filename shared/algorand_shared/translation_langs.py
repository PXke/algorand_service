"""Article translation target languages, shared between backend and workers.

Both `backend/app/core/article_translation_langs.py` and
`workers/app/core/article_translation_langs.py` hand-maintained an identical
copy of this tuple + dict (each with a "keep in sync with the other file"
comment). Centralized here so there is exactly one place to add or rename a
target language.
"""

from __future__ import annotations

# Corridor-first: HesabPay / UNDP regions (Afghanistan, Syria), then global.
#
# `fa` targets STANDARD PERSIAN (Farsi), not Afghan Dari, as of 2026-07-29.
# The two are mutually intelligible -- the literature puts the gap at roughly
# European vs Canadian French -- so Afghan readers lose nothing, while the
# audience widens from Afghanistan alone to the whole Persian continuum
# (Iran, Afghanistan, Tajik diaspora). Farsi is also far better resourced in
# machine translation than Dari, so the output quality improves for free.
# The hreflang is unqualified `fa` for the same reason: `fa-AF` volunteered a
# regional restriction we do not want.
ARTICLE_TRANSLATION_LANGS: tuple[str, ...] = (
    "fa",  # Persian/Farsi -- Iran + Afghanistan (HesabPay corridor)
    "ps",  # Pashto -- Afghanistan (HesabPay)
    "ar",  # Arabic -- Syria, Sudan
    "ru",  # Russian -- Central Asia diaspora
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
