"""Autonomous mode for brand-new content (owner decision 2026-07-12): a compose the classifier wasn't confident about no longer always waits for a human review click — it auto-approves when it clears a strict AND-gate over grade / headline / gatekeeper factuality+completeness (gate.passed), similar in shape to recompose_published's auto-apply gate (see test_recompose_autonomous.py) but stricter: recompose deliberately drops completeness from its own gate (see that file), fresh candidates do not, just gated by FRESH_AUTO_APPROVE_* config instead of RECOMPOSE_AUTO_APPLY_*. Any missing or failing signal must fail CLOSED to manual review, never open.

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
    """Approves when enabled, grade clears the floor, the title is clean, and the gate passed."""
    assert _fresh_auto_approve_decision(
        enabled=True, grade=8.6, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=True
    )


def test_disabled_flag_blocks_regardless_of_quality() -> None:
    """Blocks auto-approval when the feature flag is off even with a perfect grade."""
    assert not _fresh_auto_approve_decision(
        enabled=False, grade=10.0, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=True
    )


def test_grade_below_floor_blocks() -> None:
    """Blocks a grade that clears the looser classifier-confident bar but not the strict fresh floor."""
    # Below the strict 8.0 floor even though it would have cleared the looser
    # 6.0 bar the classifier-confident lane uses — brand-new content gets no
    # discount just because a human has never seen it.
    assert not _fresh_auto_approve_decision(
        enabled=True, grade=6.5, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=True
    )


def test_missing_grade_fails_closed() -> None:
    """Blocks auto-approval when the grade is None."""
    assert not _fresh_auto_approve_decision(
        enabled=True, grade=None, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=True
    )


def test_colon_label_headline_blocks_even_with_perfect_grade() -> None:
    """Blocks auto-approval on a colon-labeled headline even with a perfect grade."""
    assert not _fresh_auto_approve_decision(
        enabled=True, grade=10.0, floor=_FLOOR, title=_COLON_TITLE, gate_ok=True
    )


_FACT_MIN = 0.80


def test_low_factuality_blocks() -> None:
    """Blocks auto-approval when the gatekeeper's factuality score is low."""
    gate = DeterministicGate(factuality_score=0.4, completeness_passed=True, passed=False)
    gate_ok = gate.passed
    assert not _fresh_auto_approve_decision(
        enabled=True, grade=9.0, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=gate_ok
    )


def test_completeness_fail_alone_blocks() -> None:
    """Unlike recompose (which drops completeness from its gate — audited 2026-07-12 to false-positive on ~all Tier-2 rewrites regardless of quality), fresh candidates are exactly what completeness's domain_provenance check exists to triage: a human has never seen this content before. gate_ok must use gate.passed (factuality AND completeness), not factuality alone — a bare completeness failure like domain_provenance previously slipped through as diverted_by="classifier" and auto-approved into the backlog with zero human review (d13.co, 2026-07-23)."""
    gate = DeterministicGate(
        factuality_score=0.95,
        completeness_passed=False,
        passed=False,
        failed_rules=("domain_provenance",),
    )
    gate_ok = gate.passed
    assert not _fresh_auto_approve_decision(
        enabled=True, grade=9.0, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=gate_ok
    )


def test_gatekeeper_disabled_entirely_does_not_block() -> None:
    """gate_draft() returns None when GATEKEEPER_ENABLED is off — no signal to fail on, so _fresh_auto_approve_passes treats that case as gate_ok=True."""
    gate = None
    gate_ok = True if gate is None else gate.passed
    assert _fresh_auto_approve_decision(
        enabled=True, grade=9.0, floor=_FLOOR, title=_GOOD_TITLE, gate_ok=gate_ok
    )


def test_defunct_domain_blocks_auto_approve_before_any_grading() -> None:
    """A defunct-entity hit must fail auto-approve closed on its own — the real _fresh_auto_approve_passes short-circuits BEFORE grading/gatekeeper (which can pass on a draft recommending a dead entity, as the MyAlgo draft did), so this call touches no Cassandra/Mistral. Guards the auto-approve bypass that would otherwise re-open a held defunct draft (2026-07-19)."""
    from app.modules.newspaper.tasks.publish_tasks import _fresh_auto_approve_passes

    passed, meta = _fresh_auto_approve_passes(
        title=_GOOD_TITLE,
        body="Use [MyAlgo](https://wallet.myalgo.com).",
        page_text="wallets",
        source_url="editorial://brief/x",
        defunct_domains=("wallet.myalgo.com",),
    )
    assert passed is False
    assert meta["auto_applied"] == "0"
    assert "wallet.myalgo.com" in meta["defunct_domains"]


def test_unsourced_specifics_block_auto_approve_before_any_grading() -> None:
    """An unsourced-specifics hold must fail auto-approve closed on its own too: grade/headline/gatekeeper can't see the research trace, so a draft asserting a fabricated "1,000 issuers" would otherwise clear the AND-gate and re-open the hold (GoPlausible incident 2026-07-20).

    Short-circuits before any grading, so no Cassandra/Mistral is touched.
    """
    from app.modules.newspaper.tasks.publish_tasks import _fresh_auto_approve_passes

    passed, meta = _fresh_auto_approve_passes(
        title=_GOOD_TITLE,
        body="The platform has over 1,000 issuers.",
        page_text="platform",
        source_url="editorial://brief/x",
        unsourced_hold_reason="unsourced hard specifics not in research: 1,000 (issuers)",
    )
    assert passed is False
    assert meta["auto_applied"] == "0"
    assert "1,000" in meta["unsourced_hold_reason"]


def test_broken_link_claim_blocks_auto_approve_before_any_grading() -> None:
    """An unverified broken-link claim must fail auto-approve closed on its own too: grade/headline/gatekeeper can't see whether the writer ever tried click_element, so a draft wrongly calling a real JS-modal feature "broken" would otherwise clear the AND-gate and re-open the hold (lumirogue.com, recurred 2026-08-10 and 2026-08-12).

    Short-circuits before any grading, so no Cassandra/Mistral is touched.
    """
    from app.modules.newspaper.tasks.publish_tasks import _fresh_auto_approve_passes

    passed, meta = _fresh_auto_approve_passes(
        title=_GOOD_TITLE,
        body="The Terms of use page returns a 404.",
        page_text="platform",
        source_url="editorial://brief/x",
        broken_link_hold_reason=(
            "unverified broken-link claim(s), no click_element/play_interactive "
            "click attempted this session: returns a 404"
        ),
    )
    assert passed is False
    assert meta["auto_applied"] == "0"
    assert "404" in meta["broken_link_hold_reason"]
