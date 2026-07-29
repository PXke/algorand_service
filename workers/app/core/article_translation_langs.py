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
    "fa": "Persian (Farsi)",
    "ps": "Pashto",
    "ar": "Arabic",
    "ru": "Russian",
    "zh": "Chinese (Simplified)",
    "hi": "Hindi",
    "es": "Spanish (Castilian)",
    "fr": "French",
}

# Per-language term policy for the translator.
#
# Why this exists: crypto vocabulary has a settled convention in each language's
# own press, and it is NOT "translate everything" or "keep everything in
# English" — it differs per term AND per language. Left to decide ad hoc, the
# model picks differently in every article (observed on the live corpus:
# "staking" rendered as an anglicism in some French paragraphs and calqued in
# others, within the same piece). That inconsistency reads as machine output
# even when each individual choice is defensible.
#
# Rule of thumb encoded below: Latin-script targets keep the English term where
# the local crypto press does ("staking", "smart contract" in French/Spanish);
# Arabic-script and CJK targets take the established native term where one
# exists, because their press does transliterate or calque loanwords. Brand and
# protocol names (Algorand, ALGO, CompX, x402) are never translated in any
# language — that rule lives in the system prompt, not here.
ARTICLE_TRANSLATION_GLOSSARY: dict[str, dict[str, str]] = {
    "fr": {
        "staking": "staking (NOT jalonnement)",
        "smart contract": "smart contract (NOT contrat intelligent)",
        "blockchain": "blockchain",
        "wallet": "portefeuille",
        "block": "bloc",
        "fee": "frais",
        "ledger": "registre",
        "settlement": "règlement",
        "yield": "rendement",
        "governance": "gouvernance",
    },
    "es": {
        "staking": "staking (NOT apuesta/participación)",
        "smart contract": "contrato inteligente",
        "blockchain": "blockchain",
        "wallet": "billetera",
        "block": "bloque",
        "fee": "comisión",
        "ledger": "libro mayor",
        "settlement": "liquidación",
        "yield": "rendimiento",
        "governance": "gobernanza",
    },
    "ru": {
        "staking": "стейкинг",
        "smart contract": "смарт-контракт",
        "blockchain": "блокчейн",
        "wallet": "кошелёк",
        "block": "блок",
        "fee": "комиссия",
        "ledger": "реестр",
        "settlement": "расчёты",
        "yield": "доходность",
        "governance": "управление",
    },
    "zh": {
        "staking": "质押",
        "smart contract": "智能合约",
        "blockchain": "区块链",
        "wallet": "钱包",
        "block": "区块",
        "fee": "手续费",
        "ledger": "账本",
        "settlement": "结算",
        "yield": "收益",
        "governance": "治理",
    },
    "ar": {
        "staking": "التخزين (staking)",
        "smart contract": "العقد الذكي",
        "blockchain": "البلوك تشين",
        "wallet": "المحفظة",
        "block": "الكتلة",
        "fee": "الرسوم",
        "ledger": "السجل",
        "settlement": "التسوية",
        "yield": "العائد",
        "governance": "الحوكمة",
    },
    "fa": {
        "staking": "استیکینگ",
        "smart contract": "قرارداد هوشمند",
        "blockchain": "بلاک‌چین",
        "wallet": "کیف پول",
        "block": "بلاک",
        "fee": "کارمزد",
        "ledger": "دفتر کل",
        "settlement": "تسویه",
        "yield": "بازدهی",
        "governance": "حاکمیت",
    },
    "ps": {
        "staking": "سټیکینګ",
        "smart contract": "هوښیار تړون",
        "blockchain": "بلاک چین",
        "wallet": "بټوه",
        "block": "بلاک",
        "fee": "فیس",
        "ledger": "خط کتاب",
        "settlement": "تصفیه",
        "yield": "عاید",
        "governance": "حکومتوالي",
    },
    "hi": {
        "staking": "स्टेकिंग",
        "smart contract": "स्मार्ट कॉन्ट्रैक्ट",
        "blockchain": "ब्लॉकचेन",
        "wallet": "वॉलेट",
        "block": "ब्लॉक",
        "fee": "शुल्क",
        "ledger": "बहीखाता",
        "settlement": "निपटान",
        "yield": "प्रतिफल",
        "governance": "गवर्नेंस",
    },
}


# Which numeral glyphs each language should use, and stick to.
#
# Measured on the live corpus 2026-07-29: `ar` and `hi` came back in Western
# digits with ZERO native glyphs across every stored translation, so their
# entries just make the existing behaviour explicit. `fa` and `ps` are the
# problem — both MIX systems (`fa` ~64% Extended Arabic-Indic / ~36% Western,
# `ps` roughly the reverse), which means one publication renders a figure in
# Persian digits in one paragraph and Western digits in the next. That is
# reader-visible sloppiness with nothing to do with translation accuracy, and
# nothing in the prompt ever said which to use.
#
# `fa` -> Extended Arabic-Indic follows standard Persian press convention.
# `ps` is set to match it (same script, same region) but is PROVISIONAL:
# unlike Persian there is no established Pashto crypto press to check against,
# so a native reviewer should confirm it. Consistency is enforced either way —
# picking the wrong system consistently is still better than mixing.
ARTICLE_TRANSLATION_DIGITS: dict[str, str] = {
    "fa": "Extended Arabic-Indic (۰۱۲۳۴۵۶۷۸۹)",
    "ps": "Extended Arabic-Indic (۰۱۲۳۴۵۶۷۸۹)",
    "ar": "Western Arabic (0123456789)",
    "hi": "Western Arabic (0123456789)",
    "ru": "Western Arabic (0123456789)",
    "zh": "Western Arabic (0123456789)",
    "es": "Western Arabic (0123456789)",
    "fr": "Western Arabic (0123456789)",
}


def digits_block(lang: str) -> str:
    """Prompt fragment pinning this language's numeral system, or "" when unset."""
    system = ARTICLE_TRANSLATION_DIGITS.get(lang)
    if not system:
        return ""
    return (
        f"\n\nNUMERALS — write every number in {system}, the same way throughout "
        "the article. Never mix numeral systems within one article. Digits, "
        "separators and the value itself must match the source exactly; only the "
        "glyphs may change."
    )


def glossary_block(lang: str) -> str:
    """Prompt fragment pinning this language's crypto terminology, or "" when none is defined."""
    terms = ARTICLE_TRANSLATION_GLOSSARY.get(lang)
    if not terms:
        return ""
    lines = "\n".join(f"- {src} -> {dst}" for src, dst in terms.items())
    return (
        "\n\nTERMINOLOGY — use exactly these renderings, consistently, "
        "every time the term appears:\n" + lines
    )
