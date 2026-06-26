"""Prior-corrected inference math (torch-free). Locks down the fix for the
logit-cancellation bug: the shift is applied once, at inference, in the right
direction."""

import math

from app.modules.gatekeeper import inference as inf


def test_logit_adjustment_sign() -> None:
    # Rare failures -> negative shift (pull probabilities down).
    assert inf.logit_adjustment(0.05) < 0
    # 50/50 prior -> zero shift (the balanced training case).
    assert abs(inf.logit_adjustment(0.5)) < 1e-12
    assert inf.logit_adjustment(0.8) > 0


def test_calibrate_pulls_down_for_rare_failures() -> None:
    # A borderline raw logit of 0 (balanced p=0.5) must drop well below 0.5
    # once the true 5% base rate is applied — the anti-over-rejection effect.
    p = inf.calibrate(0.0, base_fail_rate=0.05)
    assert p < 0.1
    # And exactly recovers 0.5 when the base rate is balanced.
    assert abs(inf.calibrate(0.0, base_fail_rate=0.5) - 0.5) < 1e-9


def test_calibrate_matches_closed_form() -> None:
    z, p = 1.3, 0.07
    c = math.log(p / (1 - p))
    expected = 1.0 / (1.0 + math.exp(-(z + c)))
    assert abs(inf.calibrate(z, p) - expected) < 1e-12


def test_dynamic_base_rate_no_retrain() -> None:
    # Same raw logit, higher production failure rate -> higher calibrated prob,
    # with no model change. This is the "update base_fail_rate, don't retrain".
    assert inf.calibrate(0.5, 0.05) < inf.calibrate(0.5, 0.15)


def test_quality_grade_is_plain_sigmoid_no_prior() -> None:
    # Quality is a grade, not a rare-event gate: no class-prior shift.
    assert abs(inf.quality_grade(0.0) - 0.5) < 1e-9
    assert inf.quality_grade(3.0) > 0.9   # high logit -> good article
    assert inf.quality_grade(-3.0) < 0.1  # low logit -> poor article


def test_decide_completeness_first() -> None:
    d = inf.decide(
        raw_factuality=-5.0, raw_tone=-5.0,
        base_fail_rate_factuality=0.05, base_fail_rate_tone=0.05,
        threshold_factuality=0.5, threshold_tone=0.5,
        completeness_passed=False,
    )
    assert d.decision == "RETRY_COMPLETENESS"


def test_decide_routes_clean_draft() -> None:
    d = inf.decide(
        raw_factuality=-4.0, raw_tone=-4.0,
        base_fail_rate_factuality=0.05, base_fail_rate_tone=0.05,
        threshold_factuality=0.5, threshold_tone=0.5,
        completeness_passed=True,
    )
    assert d.decision == "ROUTE"


def test_decide_drops_on_factuality() -> None:
    d = inf.decide(
        raw_factuality=6.0, raw_tone=-4.0,
        base_fail_rate_factuality=0.05, base_fail_rate_tone=0.05,
        threshold_factuality=0.5, threshold_tone=0.5,
        completeness_passed=True,
    )
    assert d.decision == "DROP_FACTUALITY"
