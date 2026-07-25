"""Annotator: raw (source, trace, article) -> ``AnnotatedSample`` for Layer 1.

Two tiers, cheapest first (the design from the spec review):

- Tier 1 (deterministic, free): numeric grounding via ``fact_align``. High
  precision — an article figure with no trace anchor is a hard factuality signal.
  Kept numeric-only on purpose; entity/semantic calls are too noisy to auto-label
  deterministically and are deferred to Tier 2.
- Tier 2 (LLM): the semantic modes Tier 1 can't see (relational hallucination,
  unsupported non-numeric claims, tone) and disambiguation of Tier-1 candidates.
  This is a NARROW diagnostic classification of a known output against a
  ground-truth trace — not the collapse-prone "Large grades Mini" loop — and is
  meant to be validated against the human anchors before it's trusted.

The LLM is injected as a ``classify`` callable so the core stays import-light and
unit-testable; ``mistral_classifier()`` is the production adapter. Everything is
failure-tolerant: a broken/missing classifier degrades to Tier-1 only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.modules.gatekeeper.fact_align import numeric_entailment_score
from app.modules.gatekeeper.profile import AnnotatedSample

# Closed taxonomy. Tier-2 labels outside this set are treated as "unclassified"
# (the novel-failure-mode canary) rather than silently trusted.
FACTUALITY_TYPES = frozenset(
    {
        "numeric_drift",
        "unsupported_elaboration",
        "entity_swap",
        "cross_contamination",
        "relational_hallucination",
        "temporal_collapse",
    }
)
TONE_TYPES = frozenset({"hype", "speculative_tone", "clickbait"})
TAXONOMY = FACTUALITY_TYPES | TONE_TYPES

# classify(source, trace, article) -> raw dict (see _coerce_tier2 for the schema).
ClassifyFn = Callable[[str, str, str], dict]


@dataclass(frozen=True)
class Tier1Annotation:
    """Deterministic (rule-based) numeric-grounding annotation."""
    factuality_fail: bool
    error_types: tuple[str, ...]
    severities: dict[str, float]
    entailment: float
    ungrounded: tuple[str, ...]


@dataclass(frozen=True)
class Tier2Annotation:
    """LLM-judged annotation, unioned with the Tier-1 result."""
    factuality_fail: bool
    tone_fail: bool
    error_types: tuple[str, ...]
    severities: dict[str, float]
    unclassified: bool  # the LLM saw a failure it couldn't map to TAXONOMY
    confidence: float


def tier1_annotate(
    source_text: str,  # noqa: ARG001 -- name must match the real callee's keyword arg
    trace_text: str,
    article_text: str,
    *,
    fact_min: float = 0.80,
) -> Tier1Annotation:
    """Deterministic numeric grounding. Ungrounded figures are tagged ``unsupported_elaboration`` (the dominant real mode); Tier 2 may refine that to ``numeric_drift`` when it can tell a perturbed value from an invented one."""
    fact = numeric_entailment_score(trace_text, article_text)
    fail = fact.score < fact_min
    error_types: tuple[str, ...] = ()
    severities: dict[str, float] = {}
    if fail:
        error_types = ("unsupported_elaboration",)
        severities = {"unsupported_elaboration": round(1.0 - fact.score, 3)}
    return Tier1Annotation(
        factuality_fail=fail,
        error_types=error_types,
        severities=severities,
        entailment=fact.score,
        ungrounded=fact.ungrounded,
    )


def _coerce_tier2(raw: dict) -> Tier2Annotation:
    """Defensively parse the LLM's JSON.

    Expected schema (all optional): {factuality_fail: bool, tone_fail: bool,
    error_types: [str], severities: {str: 0..1}, unclassified: bool,
    confidence: 0..1}. Labels outside TAXONOMY are dropped and flip
    ``unclassified`` on.
    """
    reported = [str(t) for t in (raw.get("error_types") or [])]
    known = tuple(t for t in reported if t in TAXONOMY)
    unknown = [t for t in reported if t not in TAXONOMY]
    raw_sev = raw.get("severities") or {}
    severities = {t: max(0.0, min(1.0, float(raw_sev.get(t, 0.7)))) for t in known}
    return Tier2Annotation(
        factuality_fail=bool(raw.get("factuality_fail", any(t in FACTUALITY_TYPES for t in known))),
        tone_fail=bool(raw.get("tone_fail", any(t in TONE_TYPES for t in known))),
        error_types=known,
        severities=severities,
        unclassified=bool(raw.get("unclassified", False)) or bool(unknown),
        confidence=max(0.0, min(1.0, float(raw.get("confidence", 0.5)))),
    )


def tier2_annotate(
    source_text: str, trace_text: str, article_text: str, classify: ClassifyFn
) -> Tier2Annotation | None:
    """Run the injected LLM classifier. Returns None on any failure so the caller falls back to Tier-1-only labels."""
    try:
        raw = classify(source_text, trace_text, article_text)
        return _coerce_tier2(raw if isinstance(raw, dict) else {})
    except Exception:
        return None


def annotate(
    source_text: str,
    trace_text: str,
    article_text: str,
    *,
    classify: ClassifyFn | None = None,
    fact_min: float = 0.80,
    sample_source: str = "devtrace",
) -> AnnotatedSample:
    """Full annotation -> a Layer-1 ``AnnotatedSample``. Tier-1 is authoritative for numeric grounding; Tier-2 unions in semantic + tone findings."""
    t1 = tier1_annotate(source_text, trace_text, article_text, fact_min=fact_min)
    error_types = set(t1.error_types)
    severities = dict(t1.severities)
    fact_fail = t1.factuality_fail
    tone_fail = False

    if classify is not None:
        t2 = tier2_annotate(source_text, trace_text, article_text, classify)
        if t2 is not None:
            fact_fail = fact_fail or t2.factuality_fail
            tone_fail = t2.tone_fail
            for et in t2.error_types:
                error_types.add(et)
                severities.setdefault(et, t2.severities.get(et, 0.7))

    return AnnotatedSample(
        factuality_fail=fact_fail,
        tone_fail=tone_fail,
        error_types=tuple(sorted(error_types)),
        severities=severities,
        source=sample_source,
    )


_TIER2_SYSTEM = (
    "You are a strict fact-checking classifier for an Algorand news pipeline. "
    "You are given an article and the GROUND-TRUTH research trace it was written "
    "from. Judge ONLY against the trace and source — never outside knowledge. "
    "Identify failures and return JSON with keys: factuality_fail (bool), "
    "tone_fail (bool), error_types (subset of: numeric_drift, "
    "unsupported_elaboration, entity_swap, cross_contamination, "
    "relational_hallucination, hype, speculative_tone, clickbait), severities "
    "(map error_type -> 0.0..1.0 how severe), unclassified (true if you see a "
    "failure that fits NONE of the listed types), confidence (0.0..1.0). "
    "A claim with no support in the trace is a failure even if plausible."
)


def mistral_classifier(*, max_tokens: int = 600) -> ClassifyFn:
    """Production Tier-2 adapter: a ``classify`` callable backed by the Mistral client at temperature 0.0 (diagnostic, not creative). Lazy so importing this module never pulls the client."""
    from app.modules.ai.mistral_client import get_mistral_client

    client = get_mistral_client()

    def classify(source_text: str, trace_text: str, article_text: str) -> dict:
        user = (
            f"TRACE (ground truth):\n{trace_text[:6000]}\n\n"
            f"SOURCE:\n{source_text[:4000]}\n\n"
            f"ARTICLE:\n{article_text[:6000]}"
        )
        messages = [
            {"role": "system", "content": _TIER2_SYSTEM},
            {"role": "user", "content": user},
        ]
        return client.chat_json_object(messages, temperature=0.0, max_tokens=max_tokens)

    return classify
