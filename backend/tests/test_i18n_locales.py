"""The UI locale files must stay key-complete, and language names must not be translated.

Two failure modes seen in production, both from running the locale files through
a translation pass that could not tell prose from a proper noun:

  * Language names are ENDONYMS — a picker lists every language in its own
    language, so "中文" is "中文" in the French file too. Translating them gave
    French "Chine" (the country), Hindi "धन्यवाद" ("thank you") for Arabic, and
    Chinese "巴黎" ("Paris") for Arabic and "法国人" ("a French person") for French.
  * Keys drift: every locale was 28 keys behind en.json while carrying 3 keys
    that no longer existed in the source, so those strings silently fell back.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

LOCALES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "i18n" / "locales"

# The docker test image copies backend/, workers/, shared/ and deploy/ — not
# frontend/ (docker/Dockerfile). These are repo-level checks that happen to run
# under the backend suite because it is the only pytest harness in the repo, so
# skip rather than fail where the sources genuinely are not present.
pytestmark = pytest.mark.skipif(
    not LOCALES.is_dir(), reason=f"frontend locales not present at {LOCALES}"
)

# localeSystem is deliberately absent: it is prose about the device, not a
# language name, and SHOULD be translated.
ENDONYMS = {
    "localeEnglish": "English",
    "localeSpanish": "Español",
    "localeFrench": "Français",
    "localeArabic": "العربية",
    "localeChinese": "中文",
    "localeHindi": "हिन्दी",
    "localeRussian": "Русский",
    "localeDari": "دری",
    "localePashto": "پښتو",
}


def _load(name: str) -> dict[str, object]:
    return json.loads((LOCALES / f"{name}.json").read_text(encoding="utf-8"))


def _locale_names() -> list[str]:
    return sorted(p.stem for p in LOCALES.glob("*.json") if p.stem != "en")


def test_locales_exist() -> None:
    """Guard against the glob silently matching nothing and vacating every check."""
    assert _locale_names(), f"no locale files found under {LOCALES}"


@pytest.mark.parametrize("lang", _locale_names())
def test_language_names_are_endonyms(lang: str) -> None:
    """Each language is named in its own language, identically in every file."""
    data = _load(lang)
    wrong = {k: data[k] for k, want in ENDONYMS.items() if k in data and data[k] != want}
    assert not wrong, (
        f"{lang}.json translated language names that must stay endonyms: {wrong}. "
        f"A language picker lists every language in its own language."
    )


@pytest.mark.parametrize("lang", _locale_names())
def test_locale_has_exactly_the_english_keys(lang: str) -> None:
    """No missing keys (silent English fallback) and no stale ones (dead weight)."""
    en, data = _load("en"), _load(lang)
    missing = sorted(set(en) - set(data))
    stale = sorted(set(data) - set(en))
    assert not missing, f"{lang}.json is missing {len(missing)} keys: {missing[:8]}"
    assert not stale, f"{lang}.json has {len(stale)} keys absent from en.json: {stale[:8]}"


@pytest.mark.parametrize("lang", _locale_names())
def test_placeholders_survive_translation(lang: str) -> None:
    """{tag}/{host} interpolation breaks silently if a translator drops or renames one."""
    en, data = _load("en"), _load(lang)
    for key, source in en.items():
        if not isinstance(source, str) or key not in data:
            continue
        target = data[key]
        if not isinstance(target, str):
            continue
        want = set(re.findall(r"\{(\w+)\}", source))
        got = set(re.findall(r"\{(\w+)\}", target))
        assert want == got, f"{lang}.json[{key}]: placeholders {want} became {got}"
