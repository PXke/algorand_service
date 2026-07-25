"""Annotator-validation harness — the gate that decides which error types the Tier-2 LLM annotator is allowed to auto-label.

The annotator produces labels; nothing should trust it until it agrees with
humans. This compares machine ``AnnotatedSample``s against the human-tagged 40
anchors and, per error type, computes precision/recall over the human tags. An
error type is "trusted" only if it clears a precision/recall bar AND has enough
human support — so a type the LLM is bad at (or that's too rare to judge) is
excluded from auto-labeling rather than silently poisoning Layer 1.

The anchors are the immutable, unbiased ground truth (never used for training).
Pure Python; unit-tested. The human tags are themselves ``AnnotatedSample``s, so
the human and machine sides share one format.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.modules.gatekeeper.profile import AnnotatedSample

# 40 anchors is the target; below this the report is statistically meaningless.
MIN_ANCHORS = 20
# Per-type bars for auto-label trust.
MIN_SUPPORT = 5  # human-positive anchors for this type
MIN_PRECISION = 0.80
MIN_RECALL = 0.70

Pair = tuple[AnnotatedSample, AnnotatedSample]  # (human, machine)


@dataclass(frozen=True)
class TypeMetrics:
    """One error type's precision/recall against the anchor pool."""
    error_type: str
    tp: int
    fp: int
    fn: int
    support: int  # human positives = tp + fn
    precision: float
    recall: float
    trusted: bool


@dataclass(frozen=True)
class ValidationReport:
    """Per-type metrics and overall agreement for a validation run."""
    per_type: dict[str, TypeMetrics]
    factuality_agreement: float  # fraction of anchors where the fail flag matched
    tone_agreement: float
    trusted_types: frozenset[str]
    n_anchors: int
    gated: bool  # True => too few anchors; trust nothing

    def summary(self) -> dict:
        """Serialize this report to a JSON-friendly summary dict."""
        return {
            "n_anchors": self.n_anchors,
            "gated": self.gated,
            "factuality_agreement": round(self.factuality_agreement, 3),
            "tone_agreement": round(self.tone_agreement, 3),
            "trusted_types": sorted(self.trusted_types),
            "per_type": {
                t: {
                    "precision": round(m.precision, 3),
                    "recall": round(m.recall, 3),
                    "support": m.support,
                    "trusted": m.trusted,
                }
                for t, m in self.per_type.items()
            },
        }


def _safe_div(a: int, b: int) -> float:
    return a / b if b else 0.0


def validate_annotator(
    pairs: Iterable[Pair],
    *,
    min_support: int = MIN_SUPPORT,
    min_precision: float = MIN_PRECISION,
    min_recall: float = MIN_RECALL,
    min_anchors: int = MIN_ANCHORS,
) -> ValidationReport:
    """Compute per-type precision/recall of the machine annotator against the human anchors and decide which types are trustworthy enough to auto-label."""
    pairs = list(pairs)
    n = len(pairs)

    # Fail-flag agreement (overall factuality/tone calls).
    fact_match = sum(1 for h, m in pairs if h.factuality_fail == m.factuality_fail)
    tone_match = sum(1 for h, m in pairs if h.tone_fail == m.tone_fail)

    # Per-type confusion over the union of all observed types.
    vocab = sorted({t for h, m in pairs for t in (*h.error_types, *m.error_types)})
    per_type: dict[str, TypeMetrics] = {}
    for t in vocab:
        tp = fp = fn = 0
        for h, m in pairs:
            in_h, in_m = t in h.error_types, t in m.error_types
            if in_h and in_m:
                tp += 1
            elif in_m and not in_h:
                fp += 1
            elif in_h and not in_m:
                fn += 1
        support = tp + fn
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, support)
        trusted = (
            n >= min_anchors
            and support >= min_support
            and precision >= min_precision
            and recall >= min_recall
        )
        per_type[t] = TypeMetrics(t, tp, fp, fn, support, precision, recall, trusted)

    gated = n < min_anchors
    trusted_types = frozenset() if gated else frozenset(t for t, m in per_type.items() if m.trusted)
    return ValidationReport(
        per_type=per_type,
        factuality_agreement=_safe_div(fact_match, n),
        tone_agreement=_safe_div(tone_match, n),
        trusted_types=trusted_types,
        n_anchors=n,
        gated=gated,
    )


def apply_trust(sample: AnnotatedSample, trusted_types: frozenset[str]) -> AnnotatedSample:
    """Drop error types the annotator isn't trusted on, so untrusted machine labels can't reach Layer 1. Numeric grounding from Tier-1 is deterministic and always kept; only LLM-contributed types are subject to the trust filter via this call at the point where machine labels are persisted."""
    kept = tuple(t for t in sample.error_types if t in trusted_types)
    severities = {t: v for t, v in sample.severities.items() if t in trusted_types}
    return AnnotatedSample(
        factuality_fail=sample.factuality_fail,
        tone_fail=sample.tone_fail,
        error_types=kept,
        severities=severities,
        source=sample.source,
    )
