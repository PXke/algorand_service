"""Adversarial synthetic-negative generator (Layer 2).

Turns a gold run (source, trace, clean article) into severity-matched negatives
without the shortcut artifacts that made the naive corruptor a keyword detector:

- Factuality mutations are *anchor-aware*: they perturb the article copy of a
  number that is actually grounded in the trace, by a sampled magnitude. A
  tolerance band keeps small perturbations labelled as POSITIVES (1.0) so the
  model learns entailment-within-tolerance, not "any change = bad".
- ``unsupported_elaboration``/tone rewrites are delegated to an injected
  ``paraphraser`` callable (the Mistral client in prod) so this module stays
  import-light and unit-testable; positives are passed through the SAME
  paraphraser to keep "LLM-touched" uncorrelated with the label.

Each output carries provenance (operator, severity) for the Layer-3 leakage
audit. Severities map to soft labels, giving the calibration curve resolution.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass

from app.modules.gatekeeper.fact_align import extract_numbers

# Within this relative drift a numeric edit is still acceptable -> POSITIVE.
TOLERANCE = 0.02


@dataclass(frozen=True)
class Sample:
    source: str
    trace: str
    article: str
    label_factuality: float   # soft target in [0,1]; 1.0 = clean
    label_tone: float
    operator: str             # provenance for the audit
    severity: float           # 0..1, drives the soft label


def _replace_first_number(article: str, raw: str, new_raw: str) -> str:
    """Replace the first verbatim occurrence of a number's source substring."""
    return article.replace(raw, new_raw, 1)


def _format_like(original_raw: str, new_value: float) -> str:
    """Render ``new_value`` echoing the original's thousands separators/decimals
    so the mutation leaves no formatting fingerprint."""
    had_comma = "," in original_raw
    had_decimal = "." in re.sub(r"[^\d.]", "", original_raw)
    if had_decimal:
        body = f"{new_value:,.2f}" if had_comma else f"{new_value:.2f}"
    else:
        body = f"{round(new_value):,}" if had_comma else f"{round(new_value)}"
    # Preserve a leading currency symbol if present.
    prefix = original_raw[0] if original_raw[:1] in "$€£" else ""
    return f"{prefix}{body}"


def _soft_label_from_severity(severity: float) -> float:
    """severity 0 -> clean (1.0); severity 1 -> definitely wrong (0.0)."""
    return round(max(0.0, min(1.0, 1.0 - severity)), 3)


def numeric_drift(
    sample_gold: Sample, rng: random.Random, *, max_pct: float = 0.5
) -> Sample | None:
    """Perturb one trace-grounded number in the article by a sampled fraction.

    The drift magnitude is sampled across the boundary: some land inside the
    tolerance band (hard POSITIVES), most outside (graded negatives). Returns
    None when no grounded number exists to perturb."""
    nums = extract_numbers(sample_gold.article)
    grounded = [n for n in nums if extract_numbers(sample_gold.trace)
                and any(abs(n.value - t.value) <= abs(n.value) * TOLERANCE
                        for t in extract_numbers(sample_gold.trace)
                        if (n.unit == "percent") == (t.unit == "percent"))]
    if not grounded:
        return None
    target = rng.choice(grounded)
    pct = rng.uniform(0.0, max_pct)
    direction = 1.0 if rng.random() < 0.5 else -1.0
    new_value = target.value * (1.0 + direction * pct)
    new_raw = _format_like(target.raw, new_value)
    severity = 0.0 if pct <= TOLERANCE else min(1.0, pct / max_pct)
    return Sample(
        source=sample_gold.source,
        trace=sample_gold.trace,
        article=_replace_first_number(sample_gold.article, target.raw, new_raw),
        label_factuality=_soft_label_from_severity(severity),
        label_tone=1.0,
        operator="numeric_drift",
        severity=round(severity, 3),
    )


def cross_contamination(
    sample_gold: Sample, donor_trace: str, rng: random.Random
) -> Sample | None:
    """Insert a real number lifted from a DIFFERENT run's trace — a plausible
    value in the wrong context. Hard negative: realistic, ungrounded here."""
    donors = extract_numbers(donor_trace)
    targets = extract_numbers(sample_gold.article)
    if not donors or not targets:
        return None
    victim = rng.choice(targets)
    donor = rng.choice(donors)
    new_raw = _format_like(victim.raw, donor.value)
    if new_raw == victim.raw:
        return None
    return Sample(
        source=sample_gold.source,
        trace=sample_gold.trace,
        article=_replace_first_number(sample_gold.article, victim.raw, new_raw),
        label_factuality=0.0,
        label_tone=1.0,
        operator="cross_contamination",
        severity=1.0,
    )


def unsupported_elaboration(
    sample_gold: Sample, paraphraser: Callable[[str], str], rng: random.Random
) -> Sample:
    """Inject a plausible, ungrounded sentence via the paraphraser (prod: the
    Mistral client). The injected fact has the same surface form as grounded
    ones, so only its lack of an anchor — not its style — marks it."""
    addition = paraphraser(sample_gold.article)
    return Sample(
        source=sample_gold.source,
        trace=sample_gold.trace,
        article=f"{sample_gold.article} {addition}".strip(),
        label_factuality=0.0,
        label_tone=1.0,
        operator="unsupported_elaboration",
        severity=1.0,
    )


def hype_rewrite(
    sample_gold: Sample, paraphraser: Callable[[str], str], rng: random.Random,
    *, intensity: float = 1.0
) -> Sample:
    """Rewrite the article into subtle hype at a sampled intensity (graded tone
    label). Vocabulary rotation lives in the injected paraphraser so the tone
    head can't memorize a fixed banlist."""
    return Sample(
        source=sample_gold.source,
        trace=sample_gold.trace,
        article=paraphraser(sample_gold.article),
        label_factuality=1.0,
        label_tone=_soft_label_from_severity(intensity),
        operator="hype_rewrite",
        severity=round(intensity, 3),
    )


def temporal_collapse(
    sample_gold: Sample, paraphraser: Callable[[str], str], rng: random.Random
) -> Sample:
    """Reframe the article's events as current/breaking ("now live", "just
    launched", "this week") WITHOUT changing the underlying facts — the temporal
    relationship is fabricated. This is the chronological-context-collapse mode
    (Tinyman/Defly): every entity is grounded, so numeric entailment passes, but
    a stale or undated event is presented as fresh news. The head can only learn
    it if the corruptor generates it — entailment/value-swap negatives never will.
    The injected ``paraphraser`` rewrites the prose with a false 'now' framing."""
    return Sample(
        source=sample_gold.source,
        trace=sample_gold.trace,
        article=paraphraser(sample_gold.article),
        label_factuality=0.0,
        label_tone=1.0,
        operator="temporal_collapse",
        severity=1.0,
    )


def symmetric_positive(
    sample_gold: Sample, paraphraser: Callable[[str], str]
) -> Sample:
    """A POSITIVE passed through the same paraphraser (neutral->neutral), so
    "LLM-touched text" is uncorrelated with the label. Without this the heads
    learn the paraphraser's fingerprint instead of the concept."""
    return Sample(
        source=sample_gold.source,
        trace=sample_gold.trace,
        article=paraphraser(sample_gold.article),
        label_factuality=1.0,
        label_tone=1.0,
        operator="symmetric_positive",
        severity=0.0,
    )
