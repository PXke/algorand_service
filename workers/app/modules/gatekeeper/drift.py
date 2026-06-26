"""Drift detection over the Stream-B audit window, with per-trigger minimum-n
gates.

Each trigger watches a different signal, needs a different sample size, and maps
to a different (differently priced) remediation. Triggers share one audit
window, so each gates on its OWN min-n: a half-full window must not trip the
calibration alarm on noise. Pure Python (+ optional scipy for the binomial
test); unit-tested directly.

  Signal            Test                 min-n              Action
  ----------------  -------------------  -----------------  ----------------------
  base-rate shift   CUSUM (uniform)      100 uniform        hot-update c (cheap)
  composition       PSI vs profile mix   50 failures        re-run Layer 1 + retrain
  calibration       ECE                  500 uniform        retrain encoder
  novel mode        binomial(unclass.)   250 uniform        human + extend taxonomy
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# Per-trigger minimum sample sizes.
MIN_N_BASE_RATE = 100
MIN_N_COMPOSITION_FAILURES = 50
MIN_N_CALIBRATION = 500
MIN_N_NOVEL_MODE = 250


@dataclass(frozen=True)
class TriggerResult:
    name: str
    fired: bool
    gated: bool            # True => not enough data yet; ``fired`` is meaningless
    statistic: float
    action: str
    detail: str = ""


# --- base-rate shift: tabular CUSUM ---------------------------------------
def cusum_base_rate(
    fail_flags: Sequence[int], p0: float, p1: float, h: float = 5.0
) -> TriggerResult:
    """Detect a sustained rise in the uniform-audit failure indicator from ``p0``
    (the rate baked into the deployed c) toward ``p1``. Fires when the upward
    cumulative sum crosses decision interval ``h``."""
    n = len(fail_flags)
    if n < MIN_N_BASE_RATE:
        return TriggerResult("base_rate_shift", False, True, 0.0,
                             "hot_update_c", f"need {MIN_N_BASE_RATE}, have {n}")
    k = 0.5 * (p1 - p0)  # reference value: half the shift worth catching
    s_plus = 0.0
    peak = 0.0
    for x in fail_flags:
        s_plus = max(0.0, s_plus + (x - (p0 + k)))
        peak = max(peak, s_plus)
    fired = peak > h
    return TriggerResult(
        "base_rate_shift", fired, False, round(peak, 4),
        "hot_update_c",
        f"CUSUM peak {peak:.3f} vs h={h}; observed rate {sum(fail_flags) / n:.3f}",
    )


# --- composition drift: Population Stability Index -------------------------
def psi(current_mix: dict[str, float], profile_mix: dict[str, float],
        n_failures: int, eps: float = 1e-4) -> TriggerResult:
    """PSI between the current error-type histogram and the corruptor's profile
    mix. >0.25 => significant shift (re-run Layer 1); 0.1-0.25 => watch."""
    if n_failures < MIN_N_COMPOSITION_FAILURES:
        return TriggerResult("composition_drift", False, True, 0.0,
                             "rerun_layer1_retrain",
                             f"need {MIN_N_COMPOSITION_FAILURES} failures, have {n_failures}")
    keys = set(current_mix) | set(profile_mix)
    value = 0.0
    for kk in keys:
        a = max(current_mix.get(kk, 0.0), eps)
        b = max(profile_mix.get(kk, 0.0), eps)
        value += (a - b) * math.log(a / b)
    return TriggerResult(
        "composition_drift", value > 0.25, False, round(value, 4),
        "rerun_layer1_retrain",
        "watch" if 0.1 < value <= 0.25 else ("shifted" if value > 0.25 else "stable"),
    )


# --- calibration decay: Expected Calibration Error ------------------------
def ece(probs: Sequence[float], labels: Sequence[float], n_bins: int = 10) -> TriggerResult:
    """Expected Calibration Error on the audit stream (labels may be soft).
    >0.05 => the encoder's confidences no longer map to reality => retrain."""
    n = len(probs)
    if n < MIN_N_CALIBRATION:
        return TriggerResult("calibration_decay", False, True, 0.0,
                             "retrain_encoder", f"need {MIN_N_CALIBRATION}, have {n}")
    bins: list[list[int]] = [[] for _ in range(n_bins)]
    for i, p in enumerate(probs):
        idx = min(n_bins - 1, max(0, int(p * n_bins)))
        bins[idx].append(i)
    value = 0.0
    for b in bins:
        if not b:
            continue
        conf = sum(probs[i] for i in b) / len(b)
        acc = sum(labels[i] for i in b) / len(b)
        value += (len(b) / n) * abs(conf - acc)
    return TriggerResult("calibration_decay", value > 0.05, False, round(value, 4),
                         "retrain_encoder", f"ECE={value:.4f}")


# --- novel failure mode: binomial test on unclassified rate ---------------
def novel_mode(n_unclassified: int, n_total: int, baseline: float = 0.0) -> TriggerResult:
    """Test whether the annotator's 'unclassified' rate exceeds the baseline by
    more than chance — the canary for failure modes the corruptor cannot
    generate by construction. Fires above a 5% floor (or significant vs
    baseline when scipy is present)."""
    if n_total < MIN_N_NOVEL_MODE:
        return TriggerResult("novel_failure_mode", False, True, 0.0,
                             "human_extend_taxonomy",
                             f"need {MIN_N_NOVEL_MODE}, have {n_total}")
    rate = n_unclassified / n_total
    try:
        from scipy.stats import binomtest

        pval = binomtest(n_unclassified, n_total, max(baseline, 1e-6),
                         alternative="greater").pvalue
        fired = rate > 0.05 and pval < 0.01
        detail = f"rate={rate:.3f}, p={pval:.4f}"
    except ImportError:
        fired = rate > 0.05
        detail = f"rate={rate:.3f} (scipy absent; floor test only)"
    return TriggerResult("novel_failure_mode", fired, False, round(rate, 4),
                         "human_extend_taxonomy", detail)
