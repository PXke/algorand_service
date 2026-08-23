"""Deterministic tool-completeness check (replaces the neural Head 2).

Predicting "should the agent have called tool X?" from text state was the
over-engineered failure point we cut from the model. The conditional rules are
business logic, so they live in plain Python where they are auditable, free, and
cannot silently regress. The check reads the source material + the tool trace
and returns a pass/fail plus the rules that fired, so a failure can be appended
to the trace and looped back to the research phase for self-correction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompletenessRule:
    """If any ``triggers`` substring appears in the source, at least one of ``required_any`` tool names must appear in the trace."""

    name: str
    triggers: tuple[str, ...]
    required_any: tuple[str, ...]


# Mandatory-check rules. Tool names match how handlers are referenced in the
# trace (see investigative_tools INVESTIGATIVE_HANDLERS keys).
DEFAULT_RULES: tuple[CompletenessRule, ...] = (
    CompletenessRule(
        name="human_identity",
        triggers=("founder", "ceo", "co-founder", "cofounder", "president", "chairman"),
        required_any=("screen_sanctions_and_pep", "query_corporate_registry"),
    ),
    # domain_provenance (trigger-word rule) removed 2026-08-21: the trigger set
    # ("http://", "https://", "www.", "website", "domain") matched nearly every
    # scraped page's own boilerplate -- confirmed live against 57 recent
    # failures including algorand.foundation, algorand.co, and perawallet.app,
    # the platform's own best-known domains -- because it scanned source_text
    # for ANY url-ish word rather than asking anything about the actual
    # article. Replaced by gate_draft's dead-domain check below, which asks a
    # narrower, answerable question straight from domain_tracking: does the
    # FINAL ARTICLE name a domain the platform already knows is dead.
    CompletenessRule(
        name="company_backing",
        triggers=("incorporated", "registered company", "ltd", "inc.", "llc", "gmbh"),
        required_any=("query_corporate_registry",),
    ),
)


@dataclass(frozen=True)
class CompletenessResult:
    """Outcome of the deterministic tool-completeness check."""

    score: float  # 1.0 pass, 0.0 any mandatory check missed
    failed_rules: tuple[str, ...] = ()
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Whether every triggered completeness rule was satisfied."""
        return self.score >= 1.0


def check_completeness(
    source_text: str,
    tool_trace: str,
    rules: Iterable[CompletenessRule] = DEFAULT_RULES,
) -> CompletenessResult:
    """Score whether the agent ran the checks its source material mandated.

    Returns score 0.0 (with the offending rule names + human-readable reasons)
    if any triggered rule went unsatisfied, else 1.0.
    """
    src = (source_text or "").lower()
    trace = tool_trace or ""
    failed: list[str] = []
    detail: dict[str, str] = {}
    for rule in rules:
        if not any(t in src for t in rule.triggers):
            continue
        if not any(tool in trace for tool in rule.required_any):
            failed.append(rule.name)
            detail[rule.name] = (
                f"source matched {rule.triggers!r} but trace called none of {rule.required_any!r}"
            )
    return CompletenessResult(
        score=0.0 if failed else 1.0,
        failed_rules=tuple(failed),
        detail=detail,
    )


# Anchored directly on a title word's actual position, requiring a Capitalized
# one-or-two-word run immediately after it ("Founder Jane Doe", "CEO Mike
# Smith"). The previous version scanned the WHOLE page for any 2+-word
# Capitalized run via extract_entities (a deliberately permissive scanner
# shared with numeric-entailment grounding, where over-matching is harmless)
# with no positional link to where a title word appeared -- so it happily
# matched marketing bullet copy anywhere on the page ("Robust Ecosystem
# Role", "Algorand Foundation", "Tinyman Farms"). A curated stoplist was
# tried first and abandoned: crypto/marketing prose is dense enough with
# Title-Case product and protocol names that the list needed for real
# coverage was unbounded whack-a-mole (found 2026-08-07 on a held Polkagold
# review row -- the stoplist version still let "Algorand Foundation" and
# "Polkadot Treasury" through). Anchoring on adjacency to the title word
# itself is what real prose naming a person actually looks like, and no
# marketing bullet phrase happens to be immediately preceded by "Founder"
# or "CEO" by coincidence.
_TITLE_WORDS = (
    "co-founder",
    "cofounder",
    "founder",
    "ceo",
    "president",
    "chairman",
    "chief",
    "mr",
    "ms",
    "dr",
)
_NAME_AFTER_TITLE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _TITLE_WORDS) + r")\.?\s+"
    r"([A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+)?)",
    re.IGNORECASE,
)


def named_persons_unscreened(source_text: str, tool_trace: str) -> list[str]:
    """Candidate person names in the source for whom no sanctions/PEP screen appears in the trace — the actionable detail behind the human_identity rule, surfaced for the self-correction prompt."""
    if "screen_sanctions_and_pep" in (tool_trace or ""):
        return []
    src = source_text or ""
    seen: dict[str, None] = {}
    for m in _NAME_AFTER_TITLE_RE.finditer(src):
        seen.setdefault(m.group(1), None)
    return list(seen)
