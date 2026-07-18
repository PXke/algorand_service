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
    [\s-]?                                          # "1,793-byte" hyphen form
    # magnitude word/letter must not run into a following word ("T" of "TPS");
    # bytes/bits/x are unit suffixes (class markers), not magnitudes
    (?P<suf>(?:billion|million|thousand|trillion|bn|mn|bytes?|bits?|[kmbt]|x)(?![A-Za-z])|%)?
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
    low = (suf or "").lower()
    if low in ("byte", "bytes"):
        return "bytes"
    if low in ("bit", "bits"):
        return "bits"
    if low == "x":
        return "multiplier"
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
            # bytes/bits/x are class markers, not magnitudes — .get default 1.0
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


# Classes a claim in the row class may be grounded by (2026-07-18, quantum
# incident): percent stays fully isolated (a bare 50 never grounds "50%").
# bits and multiplier are isolated too — that's where decorative fabrication
# lives, and plain-number collisions are exactly the false grounding to stop:
# "Falcon-1024" (an identifier) must never ground an invented "1,024-bit
# keys" claim, and a stray bare 100 must never ground a fabricated "100x"
# benchmark. bytes stays plain-compatible: trace tool output carries byte
# sizes as bare table numbers ("Falcon-1024 1793 ~1280"), so a true
# "1,793-byte" claim is genuinely grounded by them. The cost (a rare
# coincidental magnitude match) is far below the cost of flagging every true
# byte figure. Currency stays plain-compatible for the same trace-JSON
# reason (a price rides as ``0.18``).
_COMPATIBLE: dict[str, frozenset[str]] = {
    "plain": frozenset({"plain", "currency", "bytes"}),
    "currency": frozenset({"currency", "plain"}),
    "bytes": frozenset({"bytes", "plain"}),
    "percent": frozenset({"percent"}),
    "bits": frozenset({"bits"}),
    "multiplier": frozenset({"multiplier"}),
}


def _matches(a: Quantity, b: Quantity, tol: float) -> bool:
    """Two quantities entail each other: compatible unit class (see
    ``_COMPATIBLE``) and within ``tol`` relative difference (absolute when one
    side is ~0)."""
    if b.unit not in _COMPATIBLE.get(a.unit, frozenset({a.unit})):
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


_LEAD_CHARS = 500


def _past_event_dates(text: str, *, today: date) -> list[date]:
    """Dates mentioned in ``text`` that are today or earlier — roadmap/future
    mentions are excluded so they do not inflate timeliness."""
    return [d for d in extract_dates(text) if d <= today]


def event_anchor_date(
    *,
    published_at: str = "",
    page_title: str = "",
    page_text: str = "",
    today: date | None = None,
) -> date | None:
    """Best guess at when the *story* happened — not forward-looking roadmap dates.

    Priority: (1) structured page ``published_at`` metadata, (2) the most recent
    past-or-present date in the title + lead."""
    from app.modules.scraper.core.page_metadata import parse_published_date

    today = today or date.today()
    meta = parse_published_date(published_at)
    if meta is not None:
        return meta

    lead = f"{page_title}\n{page_text[:_LEAD_CHARS]}"
    past = _past_event_dates(lead, today=today)
    if past:
        return max(past)
    return None


def _timeliness_from_anchor(
    anchor: date | None,
    *,
    today: date | None = None,
    stale_days: int = 90,
    unknown_prior: float = 0.5,
) -> float:
    if anchor is None:
        return unknown_prior
    today = today or date.today()
    age = (today - anchor).days
    if age <= 0:
        return 1.0
    return max(0.0, 1.0 - age / max(1, stale_days))


def source_timeliness_score(
    *,
    published_at: str = "",
    page_title: str = "",
    page_text: str = "",
    today: date | None = None,
    stale_days: int | None = None,
    unknown_prior: float = 0.5,
) -> float:
    """0.0 = very stale, 1.0 = fresh. Used for publish-queue priority.

    ``unknown_prior`` applies when no anchor date can be inferred (typically 0.5
    so undated landing pages are neither boosted nor heavily penalized)."""
    if stale_days is None:
        from app.core.config import PAGE_STALE_MAX_AGE_DAYS

        stale_days = PAGE_STALE_MAX_AGE_DAYS
    anchor = event_anchor_date(
        published_at=published_at,
        page_title=page_title,
        page_text=page_text,
        today=today,
    )
    return _timeliness_from_anchor(
        anchor,
        today=today,
        stale_days=stale_days,
        unknown_prior=unknown_prior,
    )


def content_recency_score(
    text: str, *, today: date | None = None, stale_days: int = 365
) -> float | None:
    """Recency of draft *content* from the most recent past event date it names.

    Ignores forward-looking roadmap dates (unlike the old max-all-dates rule).
    Returns None when the text names no parseable past date, so the caller can
    apply a neutral prior."""
    today = today or date.today()
    past = _past_event_dates(text, today=today)
    if not past:
        return None
    anchor = max(past)
    age = (today - anchor).days
    if age <= 0:
        return 1.0
    return max(0.0, 1.0 - age / max(1, stale_days))
