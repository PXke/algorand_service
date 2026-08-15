"""Live deterministic pre-publish gate.

Runs the two model-free signals — completeness rules + trace<->article numeric
entailment — so the gatekeeper protects the pipeline *before* the ModernBERT
model is trained. Designed to ship in shadow mode: it always computes and
returns the signals (for the review metadata), and the caller decides whether to
enforce based on ``GATEKEEPER_ENFORCE``.

Everything here is failure-tolerant: a malformed input or missing trace yields a
permissive result, never an exception into the publish path.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.gatekeeper.completeness import check_completeness, named_persons_unscreened
from app.modules.gatekeeper.fact_align import numeric_entailment_score


@dataclass(frozen=True)
class DeterministicGate:
    """The deterministic factuality/completeness gate's verdict."""

    factuality_score: float  # grounded fraction of numeric claims
    completeness_passed: bool
    passed: bool  # overall, given the configured threshold
    reasons: tuple[str, ...] = ()  # human-readable failure reasons
    ungrounded: tuple[str, ...] = ()
    failed_rules: tuple[str, ...] = ()

    def as_metadata(self) -> dict[str, str]:
        """Compact strings for the review/grade metadata map."""
        return {
            "gk_factuality": f"{self.factuality_score:.2f}",
            "gk_completeness": "pass" if self.completeness_passed else "fail",
            "gk_passed": "1" if self.passed else "0",
            "gk_reasons": "; ".join(self.reasons)[:400],
        }


@dataclass(frozen=True)
class GateConfig:
    """Thresholds for the deterministic gate."""

    fact_min: float = 0.80
    enforce: bool = False


def run_deterministic_gate(
    source_text: str,
    tool_trace: str,
    article_text: str,
    cfg: GateConfig | None = None,
) -> DeterministicGate:
    """Compute completeness + numeric entailment for a draft. Pure (no I/O)."""
    cfg = cfg or GateConfig()
    comp = check_completeness(source_text, tool_trace)
    fact = numeric_entailment_score(tool_trace, article_text)

    reasons: list[str] = []
    if not comp.passed:
        # named_persons_unscreened is the human_identity rule's own actionable
        # detail (candidate names lacking a sanctions/PEP screen) -- attaching
        # it whenever ANY rule fails, including domain_provenance or
        # company_backing, misleadingly implies those unrelated failures are
        # about people too. Worse, on a marketing-heavy source page the
        # extractor's "two Capitalized words" proxy for a person name matches
        # plenty of non-name phrases ("Robust Ecosystem Role"), which reads
        # as noise when it's surfaced next to a failure it has nothing to do
        # with (found 2026-08-07 auditing a held Polkagold review row).
        who = ""
        if "human_identity" in comp.failed_rules:
            unscreened = named_persons_unscreened(source_text, tool_trace)
            who = f" ({', '.join(unscreened)})" if unscreened else ""
        reasons.append(f"missing mandatory checks: {', '.join(comp.failed_rules)}{who}")
    if fact.score < cfg.fact_min:
        reasons.append(
            f"{len(fact.ungrounded)} ungrounded figure(s) "
            f"[{', '.join(fact.ungrounded[:5])}] — entailment {fact.score:.2f}"
            f" < {cfg.fact_min:.2f}"
        )

    passed = comp.passed and fact.score >= cfg.fact_min
    return DeterministicGate(
        factuality_score=fact.score,
        completeness_passed=comp.passed,
        passed=passed,
        reasons=tuple(reasons),
        ungrounded=fact.ungrounded,
        failed_rules=comp.failed_rules,
    )


_SCORER: dict[str, object] = {}


def quality_proba(*, title: str, body: str, source_url: str = "") -> float | None:
    """P(good article) from the trained ModernBERT quality head, or None when GATEKEEPER_QUALITY_LIVE is off, no trained model exists, or the ML stack is absent (caller falls back to the sklearn grader, then the heuristic floor). The flag is separate from checkpoint existence: a quality-only checkpoint can be trained well before there's a gold-run corpus for factuality/tone, so serving it live is an explicit opt-in, not automatic on file presence. The scorer is cached per path — loading ModernBERT per article would be far too slow."""
    try:
        from pathlib import Path

        from app.core.config import GATEKEEPER_MODEL_PATH, GATEKEEPER_QUALITY_LIVE

        if not GATEKEEPER_QUALITY_LIVE:
            return None
        if not GATEKEEPER_MODEL_PATH or not Path(GATEKEEPER_MODEL_PATH).exists():
            return None
        from app.modules.gatekeeper.inference import GatekeeperScorer, quality_grade
        from app.modules.gatekeeper.model import build_input
        from app.modules.newspaper.investigation_store import load_investigation_trace

        scorer = _SCORER.get(GATEKEEPER_MODEL_PATH)
        if scorer is None:
            scorer = GatekeeperScorer(GATEKEEPER_MODEL_PATH)
            _SCORER[GATEKEEPER_MODEL_PATH] = scorer
        trace = load_investigation_trace(source_url) if source_url else ""
        text = build_input("", trace, f"{title}\n{body}")
        logits = scorer.raw_logits(text)  # type: ignore[attr-defined]
        return quality_grade(logits["quality"])
    except Exception:
        return None


def gate_draft(*, source_text: str, article_text: str, service_id: str) -> DeterministicGate | None:
    """Convenience wrapper for the publish task: loads the trace by service_id, reads config, runs the gate. Returns None when disabled or on any error (shadow-safe). The caller enforces only when ``GATEKEEPER_ENFORCE`` and ``not result.passed``."""
    try:
        from app.core.config import (
            GATEKEEPER_ENABLED,
            GATEKEEPER_ENFORCE,
            GATEKEEPER_FACT_MIN,
        )

        if not GATEKEEPER_ENABLED:
            return None
        from app.modules.newspaper.investigation_store import load_investigation_trace

        trace = load_investigation_trace(service_id)
        return run_deterministic_gate(
            source_text,
            trace,
            article_text,
            GateConfig(fact_min=GATEKEEPER_FACT_MIN, enforce=GATEKEEPER_ENFORCE),
        )
    except Exception:
        return None
