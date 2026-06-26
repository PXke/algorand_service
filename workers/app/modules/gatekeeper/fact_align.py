"""Deterministic trace<->article alignment (Tier-1 extractors).

Pure Python, no dependencies. Two consumers:

1. The factuality signal: ``numeric_entailment_score`` scores how much of the
   article's quantitative claims are actually grounded in the tool trace. A
   number in the prose with no matching anchor on the trace side is the cheap,
   model-free proxy for hallucinated/elaborated facts.
2. The corruptor's Tier-1 mutations: ``extract_numbers`` locates trace-grounded
   values so the mutator can perturb the *article copy* of a real fact (and
   measure the true error magnitude for severity-matched negatives).

Nothing here raises on bad input — callers run inside the failure-tolerant
pipeline, so malformed text yields empty results, never an exception.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Multipliers for magnitude suffixes/words. Order in the alternation matters:
# longer words must precede their prefixes ("billion" before "b").
_MULTIPLIER = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mn": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "trillion": 1e12,
}

# A money symbol or "USD"/"ALGO" makes a number a *currency* quantity; "%" makes
# it a *percent*; everything else is *plain*. Units only match within their class
# so "50%" never entails "$50".
_NUM_RE = re.compile(
    r"""
    (?P<cur>[$€£]|\b(?:USD|ALGO|usd|algo)\s+)?      # optional leading currency
    (?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)        # 50,000 | 50000 | 1.5
    \s?
    # magnitude word/letter must not run into a following word ("T" of "TPS")
    (?P<suf>(?:billion|million|thousand|trillion|bn|mn|[kmbt])(?![A-Za-z])|%)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Conservative entity grabber: runs of Capitalized words, $TICKERs, and domains.
# Deliberately not an NER model — it only needs to surface candidate names for
# the completeness rules and entity-swap corruptions.
_ENTITY_RE = re.compile(
    r"(\$[A-Z]{2,6}\b|[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}|[a-z0-9-]+\.(?:com|org|io|xyz|net|app|fi))"
)


@dataclass(frozen=True)
class Quantity:
    value: float          # normalized magnitude (suffix applied)
    unit: str             # "currency" | "percent" | "plain"
    raw: str              # exact source substring
    start: int
    end: int


def _unit_class(cur: str | None, suf: str | None) -> str:
    if suf == "%":
        return "percent"
    if cur:
        return "currency"
    return "plain"


def extract_numbers(text: str) -> list[Quantity]:
    """All numeric quantities in ``text``, magnitude-normalized and unit-tagged.

    "$1.5M" -> Quantity(1_500_000.0, "currency"); "12%" -> (12.0, "percent")."""
    if not text:
        return []
    out: list[Quantity] = []
    for m in _NUM_RE.finditer(text):
        try:
            base = float(m.group("num").replace(",", ""))
        except ValueError:
            continue
        suf = (m.group("suf") or "").lower()
        if suf and suf != "%":
            base *= _MULTIPLIER.get(suf, 1.0)
        out.append(
            Quantity(
                value=base,
                unit=_unit_class(m.group("cur"), suf or None),
                raw=m.group(0).strip(),
                start=m.start(),
                end=m.end(),
            )
        )
    return out


def extract_entities(text: str) -> list[str]:
    """Candidate proper-noun / ticker / domain strings (deduped, order-preserved).

    Filters single stop-capitalized words that start sentences from carrying too
    much weight is left to callers; this stays permissive on purpose."""
    if not text:
        return []
    seen: dict[str, None] = {}
    for m in _ENTITY_RE.finditer(text):
        tok = m.group(0).strip()
        if len(tok) > 2:
            seen.setdefault(tok, None)
    return list(seen)


def _matches(a: Quantity, b: Quantity, tol: float) -> bool:
    """Two quantities entail each other: compatible unit and within ``tol``
    relative difference (absolute when one side is ~0). Currency and plain are
    compatible (trace JSON carries bare numbers, e.g. a price of ``0.18``);
    percent is isolated so ``50`` never grounds ``50%``."""
    if (a.unit == "percent") != (b.unit == "percent"):
        return False
    scale = max(abs(a.value), abs(b.value))
    if scale < 1e-9:
        return abs(a.value - b.value) < 1e-9
    return abs(a.value - b.value) / scale <= tol


@dataclass(frozen=True)
class EntailmentResult:
    score: float                 # grounded fraction in [0,1] (1.0 when no claims)
    total: int                   # numeric claims found in the article
    grounded: int                # claims with a matching trace anchor
    ungrounded: tuple[str, ...]  # raw article numbers with no anchor (fabrication risk)


def numeric_entailment_score(
    trace_text: str, article_text: str, *, tol: float = 0.02
) -> EntailmentResult:
    """Fraction of the article's numeric claims that are grounded in the trace.

    ``tol`` is the tolerance band: an article number within ``tol`` relative
    difference of a trace number counts as grounded (rounding/derivation is
    acceptable). Numbers beyond every trace anchor are reported as
    ``ungrounded`` — the deterministic signal for invented or drifted figures.
    An article with no numbers is vacuously grounded (score 1.0)."""
    anchors = extract_numbers(trace_text)
    claims = extract_numbers(article_text)
    if not claims:
        return EntailmentResult(1.0, 0, 0, ())
    grounded = 0
    ungrounded: list[str] = []
    for c in claims:
        if any(_matches(c, a, tol) for a in anchors):
            grounded += 1
        else:
            ungrounded.append(c.raw)
    return EntailmentResult(
        score=grounded / len(claims),
        total=len(claims),
        grounded=grounded,
        ungrounded=tuple(ungrounded),
    )


# --- date extraction → content recency ------------------------------------
_MONTHS = {
    m: i for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"], start=1)
}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})

_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_MDY_RE = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I)
_DMY_RE = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_ALT})\.?,?\s+(\d{{4}})\b", re.I)
_MY_RE = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{4}})\b", re.I)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_dates(text: str, *, min_year: int = 1990, max_year: int | None = None) -> list[date]:
    """All calendar dates mentioned in ``text`` (deterministic parse).

    Handles ISO (2026-06-18), slashed (18/06/2026), and month-name forms
    ("June 2026", "18 June 2026", "June 18, 2026"). Slashed dates are read as
    DAY/MONTH/YEAR, falling back to MONTH/DAY when the first field is > 12.
    Years outside [min_year, today+1] are dropped as noise."""
    if not text:
        return []
    cap = (max_year or date.today().year) + 1
    out: list[date] = []

    def keep(d: date | None) -> None:
        if d and min_year <= d.year <= cap:
            out.append(d)

    for m in _ISO_RE.finditer(text):
        keep(_safe_date(int(m[1]), int(m[2]), int(m[3])))
    for m in _SLASH_RE.finditer(text):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if a > 12 >= b:          # unambiguously DAY/MONTH
            keep(_safe_date(y, b, a))
        elif b > 12 >= a:        # unambiguously MONTH/DAY
            keep(_safe_date(y, a, b))
        else:                    # ambiguous → DAY/MONTH (platform locale)
            keep(_safe_date(y, b, a))
    for m in _MDY_RE.finditer(text):
        keep(_safe_date(int(m[3]), _MONTHS[m[1].lower()], int(m[2])))
    for m in _DMY_RE.finditer(text):
        keep(_safe_date(int(m[3]), _MONTHS[m[2].lower()], int(m[1])))
    for m in _MY_RE.finditer(text):
        keep(_safe_date(int(m[2]), _MONTHS[m[1].lower()], 1))
    return out


def content_recency_score(
    text: str, *, today: date | None = None, stale_days: int = 365
) -> float | None:
    """Recency of the *content* from the most recent date it mentions (not the
    publish timestamp). 1.0 = mentions today/future, decaying to 0.0 at
    ``stale_days`` old. Returns None when the text names no parseable date, so
    the caller can apply a neutral prior."""
    dates = extract_dates(text)
    if not dates:
        return None
    today = today or date.today()
    age = (today - max(dates)).days
    if age <= 0:
        return 1.0
    return max(0.0, 1.0 - age / max(1, stale_days))
