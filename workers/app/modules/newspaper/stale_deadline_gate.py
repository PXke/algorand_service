"""Deterministic gate for a deadline/cutoff date the body frames as still open when it has already passed.

Root-caused 2026-08-04 (Meld Gold): the writer accurately pulled a real
cutoff date from the source ("Holders have until 4:00pm (AEST) on June 29,
2026, to withdraw their tokens") but the article published over five weeks
AFTER that date — the underlying fact was correct, only the tense was wrong,
presenting an already-lapsed deadline as a live, actionable one. Neither
existing recency check catches this: content_recency_score (fact_align.py)
deliberately measures "how old is the most recent date mentioned" as a
mathematical fact, which correctly scores a 36-day-old date as only mildly
stale regardless of how the surrounding prose frames it — it was never
meant to catch a grammar/calendar contradiction, only staleness of the
underlying event.

This gate is narrow on purpose: it only fires when a small, curated set of
"still open" phrases (chosen to be near-unambiguous — "have until", "is set
to", "remains open until", etc.) sits in the same sentence as a date that
has already passed by more than a grace window. A past date mentioned
WITHOUT such framing ("the notice was issued June 23, 2025") is exactly the
correct way to describe history and must never be flagged.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime

logger = logging.getLogger(__name__)


def _today() -> date:
    return datetime.now(tz=UTC).date()

# Deliberately small and specific: each phrase asserts the deadline/window is
# CURRENTLY open or upcoming. A broader net (e.g. bare "will") would flag
# ordinary future-tense prose about genuinely upcoming plans.
_STILL_OPEN_PHRASES = (
    "have until",
    "has until",
    "will have until",
    "must withdraw",
    "must complete",
    "must act",
    "must claim",
    "is set to",
    "are set to",
    "is scheduled",
    "are scheduled",
    "will be able to",
    "can still",
    "remains open until",
    "before the deadline",
    "ahead of the deadline",
    "upcoming deadline",
)

# A date this far in the past or more (relative to `today`) alongside a
# still-open phrase is treated as a genuine contradiction, not noise from a
# same-week timezone/rounding edge case.
_GRACE_DAYS = 7

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[])")


def _split_sentences(body: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(body or "") if s.strip()]


def stale_deadline_issues(body: str, *, today: date | None = None) -> list[str]:
    """Sentences that frame an already-past date as an open/upcoming deadline. Fail-open: any parsing error yields no issues rather than blocking a compose on this gate's own bug."""
    if not body:
        return []
    try:
        from app.modules.gatekeeper.fact_align import extract_dates

        as_of = today or _today()
        issues: list[str] = []
        for sentence in _split_sentences(body):
            lowered = sentence.lower()
            if not any(phrase in lowered for phrase in _STILL_OPEN_PHRASES):
                continue
            for d in extract_dates(sentence):
                age_days = (as_of - d).days
                if age_days > _GRACE_DAYS:
                    issues.append(
                        f"stale deadline: \"{d.isoformat()}\" is described as still open or "
                        f"upcoming but was {age_days} days ago (today is {as_of.isoformat()}) "
                        f"in: \"{sentence.strip()[:200]}\" — reframe in the past tense (what "
                        "happened / what the terms were) or drop the sentence if the outcome "
                        "isn't known"
                    )
        return issues
    except Exception:
        logger.warning("stale-deadline gate failed (fail-open)", exc_info=True)
        return []
