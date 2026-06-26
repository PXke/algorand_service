"""Layer-1 failure-profile estimation.

Turns annotated samples (dev-trace failures + the unbiased anchor sample) into a
``FailureProfile`` that the corruptor samples from and that ``drift.psi`` compares
against. Estimation is small-sample-robust by design:

- Laplace smoothing + a probability floor on P(type | failure) so no real-but-rare
  mode collapses to zero.
- Anchor debiasing: dev-trace failures over-represent *catchable* errors, so the
  per-type mix is reweighted toward the unbiased anchor frequencies (clipped).
- Per-head base rates come from the unbiased sample only (failures / all).

Pure Python; unit-tested. The annotator that produces ``AnnotatedSample`` rows
from raw traces is a separate concern (Tier-1 deterministic + Tier-2 LLM); this
module consumes its output.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from statistics import fmean, pstdev


@dataclass(frozen=True)
class AnnotatedSample:
    """One graded draft. ``error_types``/``severities`` describe its failures
    (empty when clean). ``source`` distinguishes the biased dev-trace pool from
    the unbiased anchor pool used for base rates and debiasing."""

    factuality_fail: bool
    tone_fail: bool
    error_types: tuple[str, ...] = ()
    severities: dict[str, float] = field(default_factory=dict)
    source: str = "devtrace"  # "devtrace" | "anchor"


@dataclass(frozen=True)
class ErrorTypeStats:
    name: str
    marginal_p: float      # smoothed, debiased P(type | failure)
    severity_mean: float
    severity_std: float
    n: int                 # raw observations backing this type


@dataclass
class FailureProfile:
    error_types: dict[str, ErrorTypeStats]
    base_fail_rate_factuality: float
    base_fail_rate_tone: float
    cardinality_mean: float          # mean #error_types per failing sample
    n_samples: int
    n_anchors: int
    provenance: dict = field(default_factory=dict)

    def composition_mix(self) -> dict[str, float]:
        """Normalized P(type | failure) histogram — the PSI baseline."""
        return {k: v.marginal_p for k, v in self.error_types.items()}

    def sample_operator(self, rng: random.Random) -> str | None:
        """Sample an error type weighted by its debiased marginal."""
        if not self.error_types:
            return None
        names = list(self.error_types)
        weights = [self.error_types[n].marginal_p for n in names]
        return rng.choices(names, weights=weights, k=1)[0]

    def sample_severity(self, op: str, rng: random.Random) -> float:
        """Sample a severity for ``op`` from its observed mean/std, clipped to
        [0,1]. Falls back to a mid severity for unknown ops."""
        st = self.error_types.get(op)
        if st is None:
            return 0.7
        if st.severity_std < 1e-6:
            return max(0.0, min(1.0, st.severity_mean))
        return max(0.0, min(1.0, rng.gauss(st.severity_mean, st.severity_std)))


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def build_profile(
    samples: list[AnnotatedSample],
    *,
    smoothing: float = 1.0,
    prob_floor: float = 0.01,
    debias_clip: tuple[float, float] = (0.25, 4.0),
) -> FailureProfile:
    """Estimate a ``FailureProfile`` from annotated samples.

    Base rates use the anchor pool when present (unbiased), else all samples.
    The type mix is Laplace-smoothed over the observed vocabulary, debiased
    toward anchor frequencies, floored, and renormalized."""
    n = len(samples)
    anchors = [s for s in samples if s.source == "anchor"]
    base_pool = anchors or samples
    nb = len(base_pool) or 1
    base_fact = sum(1 for s in base_pool if s.factuality_fail) / nb
    base_tone = sum(1 for s in base_pool if s.tone_fail) / nb

    fails = [s for s in samples if s.error_types]
    vocab = sorted({t for s in samples for t in s.error_types})

    # Raw P(type | failure) from the (biased) full failure pool.
    dev_counts = dict.fromkeys(vocab, 0)
    sev_values: dict[str, list[float]] = {t: [] for t in vocab}
    for s in fails:
        for t in s.error_types:
            dev_counts[t] += 1
            if t in s.severities:
                sev_values[t].append(s.severities[t])
    n_fail = len(fails) or 1
    denom = n_fail + smoothing * len(vocab)
    dev_mix = {t: (dev_counts[t] + smoothing) / denom for t in vocab}

    # Anchor frequencies for debiasing (only when the anchor pool has failures).
    anchor_fails = [s for s in anchors if s.error_types]
    if anchor_fails:
        ac = dict.fromkeys(vocab, 0)
        for s in anchor_fails:
            for t in s.error_types:
                ac[t] += 1
        na = len(anchor_fails)
        anchor_mix = {t: (ac[t] + smoothing) / (na + smoothing * len(vocab)) for t in vocab}
        weighted = {
            t: dev_mix[t] * _clip(anchor_mix[t] / dev_mix[t], *debias_clip) for t in vocab
        }
    else:
        weighted = dict(dev_mix)

    # Floor + renormalize.
    floored = {t: max(weighted[t], prob_floor) for t in vocab}
    total = sum(floored.values()) or 1.0
    error_types = {
        t: ErrorTypeStats(
            name=t,
            marginal_p=floored[t] / total,
            severity_mean=fmean(sev_values[t]) if sev_values[t] else 0.7,
            severity_std=pstdev(sev_values[t]) if len(sev_values[t]) > 1 else 0.0,
            n=dev_counts[t],
        )
        for t in vocab
    }

    cardinality = fmean([len(s.error_types) for s in fails]) if fails else 0.0
    return FailureProfile(
        error_types=error_types,
        base_fail_rate_factuality=base_fact,
        base_fail_rate_tone=base_tone,
        cardinality_mean=cardinality,
        n_samples=n,
        n_anchors=len(anchors),
        provenance={
            "n_failures": len(fails),
            "debiased": bool(anchor_fails),
            "vocab_size": len(vocab),
        },
    )
