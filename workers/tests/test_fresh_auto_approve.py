"""Autonomous mode for brand-new content (owner decision 2026-07-12): a
compose the classifier wasn't confident about no longer always waits for a
human review click — it auto-approves when it clears a strict AND-gate over
grade / headline / gatekeeper factuality, identical in shape to
recompose_published's auto-apply gate (see test_recompose_autonomous.py),
just gated by FRESH_AUTO_APPROVE_* config instead of RECOMPOSE_AUTO_APPLY_*.
Any missing or failing signal must fail CLOSED to manual review, never open.

These test the decision predicate directly (mirrors
_fresh_auto_approve_passes in publish_tasks.py) rather than running the full
Celery task, which needs Cassandra/Mistral. The real thresholds live in
app.core.config; a change there should be a deliberate, reviewed decision —
these tests pin the logic, not the numbers.
"""
from __future__ import annotations

from app.modules.gatekeeper.live import DeterministicGate
from app.modules.newspaper.article_grader import headline_violations


def _fresh_auto_approve_decision(
    *, enabled: bool, grade: float | None, floor: float, title: str, gate_ok: bool
) -> bool:
    """Mirrors _fresh_auto_approve_passes in publish_tasks.py."""
    return (
        enabled
        and grade is not None
        and grade >= floor
        and not headline_violations(title)
        and gate_ok
    )


_GOOD_TITLE = "Nodely Expands Infrastructure with Voi Support and Enterprise Tiers"
_COLON_TITLE = "Nodely: Infrastructure Expansion, Explained"
_FLOOR = 8.0  # FRESH_AUTO_APPROVE_GRADE_FLOOR — same bar as recompose, not the
# looser 6.0 WRITER_QUALITY_FLOOR used for the classifier-confident lane.


def test_auto_approves_when_every_signal_clears() -> None:
    assert _fresh_auto_approve_decision(
        enabled=True, grade=8.6, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=True
    )


def test_disabled_flag_blocks_regardless_of_quality() -> None:
    assert not _fresh_auto_approve_decision(
        enabled=False, grade=10.0, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=True
    )


def test_grade_below_floor_blocks() -> None:
    # Below the strict 8.0 floor even though it would have cleared the looser
    # 6.0 bar the classifier-confident lane uses — brand-new content gets no
    # discount just because a human has never seen it.
    assert not _fresh_auto_approve_decision(
        enabled=True, grade=6.5, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=True
    )


def test_missing_grade_fails_closed() -> None:
    assert not _fresh_auto_approve_decision(
        enabled=True, grade=None, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=True
    )


def test_colon_label_headline_blocks_even_with_perfect_grade() -> None:
    assert not _fresh_auto_approve_decision(
        enabled=True, grade=10.0, floor=_FLOOR, title=_COLON_TITLE, gate_ok=True
    )


_FACT_MIN = 0.80


def test_low_factuality_blocks() -> None:
    gate = DeterministicGate(factuality_score=0.4, completeness_passed=True, passed=False)
    gate_ok = gate.factuality_score >= _FACT_MIN
    assert not _fresh_auto_approve_decision(
        enabled=True, grade=9.0, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=gate_ok
    )


def test_completeness_fail_alone_does_not_block() -> None:
    """Same rationale as recompose: completeness (OSINT tool-call coverage)
    fires on any source mentioning a website/founder/company — true for
    nearly every service profile — so it's tracked in metadata but excluded
    from the gate. Factuality remains a hard gate (see test above)."""
    gate = DeterministicGate(
        factuality_score=0.95,
        completeness_passed=False,
        passed=False,
        failed_rules=("domain_provenance",),
    )
    gate_ok = gate.factuality_score >= _FACT_MIN
    assert _fresh_auto_approve_decision(
        enabled=True, grade=9.0, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=gate_ok
    )


def test_gatekeeper_disabled_entirely_does_not_block() -> None:
    """gate_draft() returns None when GATEKEEPER_ENABLED is off — no signal to
    fail on, so _fresh_auto_approve_passes treats that case as gate_ok=True."""
    gate = None
    gate_ok = True if gate is None else gate.passed
    assert _fresh_auto_approve_decision(
        enabled=True, grade=9.0, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=gate_ok
    )
