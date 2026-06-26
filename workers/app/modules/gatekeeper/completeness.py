"""Deterministic tool-completeness check (replaces the neural Head 2).

Predicting "should the agent have called tool X?" from text state was the
over-engineered failure point we cut from the model. The conditional rules are
business logic, so they live in plain Python where they are auditable, free, and
cannot silently regress. The check reads the source material + the tool trace
and returns a pass/fail plus the rules that fired, so a failure can be appended
to the trace and looped back to the research phase for self-correction.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.modules.gatekeeper.fact_align import extract_entities


@dataclass(frozen=True)
class CompletenessRule:
    """If any ``triggers`` substring appears in the source, at least one of
    ``required_any`` tool names must appear in the trace."""

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
    CompletenessRule(
        name="domain_provenance",
        triggers=("http://", "https://", "www.", "website", "domain"),
        required_any=("resolve_domain_infrastructure", "fetch_archive_snapshot"),
    ),
    CompletenessRule(
        name="company_backing",
        triggers=("incorporated", "registered company", "ltd", "inc.", "llc", "gmbh"),
        required_any=("query_corporate_registry",),
    ),
)


@dataclass(frozen=True)
class CompletenessResult:
    score: float                       # 1.0 pass, 0.0 any mandatory check missed
    failed_rules: tuple[str, ...] = ()
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.score >= 1.0


def check_completeness(
    source_text: str,
    tool_trace: str,
    rules: Iterable[CompletenessRule] = DEFAULT_RULES,
) -> CompletenessResult:
    """Score whether the agent ran the checks its source material mandated.

    Returns score 0.0 (with the offending rule names + human-readable reasons)
    if any triggered rule went unsatisfied, else 1.0."""
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
                f"source matched {rule.triggers!r} but trace called none of "
                f"{rule.required_any!r}"
            )
    return CompletenessResult(
        score=0.0 if failed else 1.0,
        failed_rules=tuple(failed),
        detail=detail,
    )


def named_persons_unscreened(source_text: str, tool_trace: str) -> list[str]:
    """Candidate person names in the source for whom no sanctions/PEP screen
    appears in the trace — the actionable detail behind the human_identity rule,
    surfaced for the self-correction prompt."""
    if "screen_sanctions_and_pep" in (tool_trace or ""):
        return []
    src = (source_text or "")
    if not any(t in src.lower() for t in ("founder", "ceo", "co-founder", "president")):
        return []
    # Two-word Capitalized runs are the cheap proxy for personal names; strip a
    # leading role/title word the greedy extractor may have swallowed.
    titles = {"founder", "ceo", "president", "chairman", "chief", "co-founder",
              "cofounder", "mr", "ms", "dr"}
    names: list[str] = []
    for e in extract_entities(src):
        if e.startswith("$") or " " not in e:
            continue
        words = e.split()
        if words[0].lower() in titles:
            words = words[1:]
        if len(words) >= 2:
            names.append(" ".join(words))
    return names
