"""Prior-corrected gatekeeper scoring.

The class-prior log-odds shift lives HERE and only here. Training is deliberately
"dumb": vanilla BCE on count-balanced batches, so the model learns the balanced
posterior ``p_bal(x)``. A single inference-time shift converts that to the real
posterior ``p_real(x)``:

    c           = log( base_fail_rate / (1 - base_fail_rate) )
    p_real(x)   = sigmoid( raw_logit + c )

Because the shift is applied exactly once and only at inference, ``base_fail_rate``
can be updated from the Stream-B audit without retraining. Applying it during
training too would cancel against this and silently redeploy the balanced model
(the cancellation bug caught in review) — so it must never move into the loss.

The pure-math helpers below need no torch and are unit-tested directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

_EPS = 1e-6


def logit_adjustment(base_fail_rate: float) -> float:
    """Log-odds of the true production prior. Negative for rare failures
    (base_fail_rate < 0.5), pulling probabilities down — fewer false rejects."""
    p = min(max(base_fail_rate, _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    # Numerically stable for large |z|.
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def calibrate(raw_logit: float, base_fail_rate: float) -> float:
    """Convert a balanced-trained raw logit to the production-calibrated
    probability of failure under the current base rate."""
    return _sigmoid(raw_logit + logit_adjustment(base_fail_rate))


@dataclass(frozen=True)
class GateDecision:
    decision: str          # ROUTE | DROP_FACTUALITY | DROP_TONE | RETRY_COMPLETENESS
    prob_factuality: float
    prob_tone: float
    raw_factuality: float
    raw_tone: float
    c_factuality: float
    c_tone: float


def decide(
    raw_factuality: float,
    raw_tone: float,
    *,
    base_fail_rate_factuality: float,
    base_fail_rate_tone: float,
    threshold_factuality: float,
    threshold_tone: float,
    completeness_passed: bool,
) -> GateDecision:
    """Full gate logic from raw head logits. Deterministic completeness is
    checked first (cheap, and routes to self-correction rather than a hard drop);
    factuality is the hard gate; tone last."""
    c_f = logit_adjustment(base_fail_rate_factuality)
    c_t = logit_adjustment(base_fail_rate_tone)
    p_f = _sigmoid(raw_factuality + c_f)
    p_t = _sigmoid(raw_tone + c_t)

    if not completeness_passed:
        decision = "RETRY_COMPLETENESS"
    elif p_f >= threshold_factuality:
        decision = "DROP_FACTUALITY"
    elif p_t >= threshold_tone:
        decision = "DROP_TONE"
    else:
        decision = "ROUTE"

    return GateDecision(
        decision=decision,
        prob_factuality=p_f, prob_tone=p_t,
        raw_factuality=raw_factuality, raw_tone=raw_tone,
        c_factuality=c_f, c_tone=c_t,
    )


class GatekeeperScorer:
    """Lazy torch-backed scorer: loads the model once, returns raw head logits
    for a built input string. Kept thin so ``decide``/``calibrate`` stay
    torch-free and testable."""

    def __init__(self, model_path: str, model_name: str | None = None):
        self._model_path = model_path
        self._model_name = model_name
        self._model: Any = None
        self._tok: Any = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import torch

        from app.modules.gatekeeper.model import (
            DEFAULT_MODEL_NAME,
            build_model,
            load_tokenizer,
        )

        name = self._model_name or DEFAULT_MODEL_NAME
        model = build_model(name)
        model.load_state_dict(torch.load(self._model_path, map_location="cpu"))
        model.eval()
        self._model = model
        self._tok = load_tokenizer(name)

    def raw_logits(self, input_text: str, max_length: int = 8192) -> dict[str, float]:
        """Raw logits for all heads: {factuality, tone, quality}."""
        self._ensure_loaded()
        import torch

        enc = self._tok(
            input_text, truncation=True, max_length=max_length, return_tensors="pt"
        )
        with torch.inference_mode():
            out = self._model(enc["input_ids"], enc["attention_mask"])
        return {
            "factuality": float(out["factuality"].item()),
            "tone": float(out["tone"].item()),
            "quality": float(out["quality"].item()),
            "relevance": float(out["relevance"].item()),
        }


def quality_grade(raw_quality: float) -> float:
    """P(good article) from the quality head — a 0..1 grade, NOT a rare-event
    gate, so no class-prior shift is applied (unlike factuality/tone). Multiply
    by 10 for the 0-10 grade the reviewer sees."""
    return _sigmoid(raw_quality)
