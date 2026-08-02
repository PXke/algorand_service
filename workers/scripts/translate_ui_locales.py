"""One-off: translate remaining English-placeholder UI strings in the frontend
locale files via the local translation engine (app.modules.ai.local_translate).

Run by hand -- matches eval_compose_prompts.py / eval_translate_candidates.py's
own convention, not wired into CI or any beat schedule:

    cd workers && .venv/bin/python -m scripts.translate_ui_locales --dry-run
    cd workers && .venv/bin/python -m scripts.translate_ui_locales
    cd workers && .venv/bin/python -m scripts.translate_ui_locales --langs fr,ar

Two failure modes this guards against explicitly (see
backend/tests/test_i18n_locales.py, written after an earlier translation pass
over this same file set hit both):

  - Endonyms (localeEnglish, localeFrench, ...) are never sent to the model --
    a language picker lists every language in its own language. Same
    treatment for the bare "PXke Algorand" brand string in appTitle, which
    this script also found corrupted (copy/pasted from pageTitleHome's
    translated value) in ar/es/fr, and mistranslated ("PXke Algorithm") in
    zh -- forced back to the brand string everywhere, not just where it was
    still untranslated.
  - {placeholder} interpolation tokens are protected before translation
    (swapped for an opaque stash token, restored after) and verified to
    survive on the other side; a string that loses one falls back to the
    English original rather than shipping broken interpolation.

ICU MessageFormat plurals (suggestionsUpvoteCount, readsCount, storiesCount)
are parsed by hand -- only the plain-text prose inside each `=N{...}`/
`other{...}` bucket is sent to the model, never the ICU syntax itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # workers/ -> "app" package
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from app.modules.ai.local_translate import (  # noqa: E402
    _translate_text_milmmt,
    _translate_text_seamless,
    engine_for,
    unload_engine,
)

LOCALES_DIR = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "i18n" / "locales"
)

# Mirrors backend/tests/test_i18n_locales.py's ENDONYMS exactly -- a language
# picker lists every language in its own language, identically in every file.
ENDONYMS = {
    "localeEnglish",
    "localeSpanish",
    "localeFrench",
    "localeArabic",
    "localeChinese",
    "localeHindi",
    "localeRussian",
    "localePersian",
    "localePashto",
}
# Proper noun, forced to this exact value in every locale regardless of its
# current content -- found corrupted (not just untranslated) in several files.
BRAND_KEYS = {"appTitle": "PXke Algorand"}
# Known copy/paste-from-a-different-key corruption -- retranslate from the
# correct English source rather than skip as "already done" (its current
# value differs from en.json's for this key, so the identical-to-en check
# alone would not catch it).
FORCE_RETRANSLATE = {("ru", "bylineChainDesk")}

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_ICU_PLURAL = re.compile(r"^\{(\w+),\s*plural,\s*(.*)\}$", re.DOTALL)
_ICU_BUCKET = re.compile(r"(=\d+|other)\{((?:[^{}]|\{[^{}]*\})*)\}")
_STASH_RE = re.compile(r"XPHXPH\s*(\d+)\s*XPHXPH")


def _translate_one(text: str, engine: str, target_language: str) -> str:
    fn = _translate_text_seamless if engine == "seq2seq" else _translate_text_milmmt
    return fn(text, target_language)


def _translate_protected(text: str, engine: str, target_language: str) -> str | None:
    """Translate one plain-text span with {placeholder} tokens protected.

    Returns None (caller should fall back to the English original) if any
    placeholder did not survive the round trip intact.
    """
    if not text.strip():
        return text
    placeholders: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        placeholders.append(m.group(0))
        return f"XPHXPH{len(placeholders) - 1}XPHXPH"

    protected = _PLACEHOLDER.sub(_stash, text)
    translated = _translate_one(protected, engine, target_language)

    def _restore(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        return placeholders[idx] if idx < len(placeholders) else m.group(0)

    restored = _STASH_RE.sub(_restore, translated)
    for ph in placeholders:
        if ph not in restored:
            return None
    return restored


def _translate_icu_plural(text: str, engine: str, target_language: str) -> str | None:
    m = _ICU_PLURAL.match(text)
    if not m:
        return None
    var, body = m.group(1), m.group(2)
    if not _ICU_BUCKET.search(body):
        return None
    out_parts = []
    for bm in _ICU_BUCKET.finditer(body):
        selector, content = bm.group(1), bm.group(2)
        translated_content = _translate_protected(content, engine, target_language)
        if translated_content is None:
            return None
        out_parts.append(f"{selector}{{{translated_content}}}")
    return "{" + var + ", plural, " + " ".join(out_parts) + "}"


def translate_ui_string(text: str, engine: str, target_language: str) -> str | None:
    """Translate one locale value, routing ICU plurals through the bucket-aware path."""
    if _ICU_PLURAL.match(text):
        return _translate_icu_plural(text, engine, target_language)
    return _translate_protected(text, engine, target_language)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--langs", default="", help="comma-separated, default: all non-en locales")
    args = parser.parse_args()

    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    all_langs = sorted(p.stem for p in LOCALES_DIR.glob("*.json") if p.stem != "en")
    langs = [lang.strip() for lang in args.langs.split(",") if lang.strip()] or all_langs

    jobs: dict[str, list[tuple[str, str]]] = {}
    locale_data: dict[str, dict[str, object]] = {}
    for lang in langs:
        path = LOCALES_DIR / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        locale_data[lang] = data
        for key, en_value in en.items():
            if not isinstance(en_value, str) or key in ENDONYMS:
                continue
            if key in BRAND_KEYS:
                if data.get(key) != BRAND_KEYS[key]:
                    print(f"[{lang}] {key}: forcing brand value (was {data.get(key)!r})")
                    data[key] = BRAND_KEYS[key]
                continue
            if data.get(key) == en_value or (lang, key) in FORCE_RETRANSLATE:
                jobs.setdefault(engine_for(lang), []).append((lang, key))

    total = sum(len(v) for v in jobs.values())
    print(f"{total} strings to translate across {len(langs)} locale(s)")
    for engine, pairs in jobs.items():
        print(f"  engine={engine}: {len(pairs)}")
    if args.dry_run:
        return

    done = 0
    for engine, pairs in jobs.items():
        print(f"=== engine={engine}, {len(pairs)} jobs ===", flush=True)
        for lang, key in pairs:
            source = en[key]
            result = translate_ui_string(source, engine, lang)
            done += 1
            if result is None:
                print(f"[{done}/{total}] SKIP (placeholder loss) {lang}.{key}", flush=True)
                continue
            locale_data[lang][key] = result
            print(f"[{done}/{total}] {lang}.{key}: {result[:70]!r}", flush=True)
        unload_engine(engine)

    for lang, data in locale_data.items():
        path = LOCALES_DIR / f"{lang}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
