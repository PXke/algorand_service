"""Layer-1 profile: base rates from the unbiased pool, smoothed/floored mix,
anchor debiasing, and sampling that the corruptor drives off."""

import random

from app.modules.gatekeeper.profile import AnnotatedSample, build_profile


def _fail(types, sev=None, fact=True, tone=False, source="devtrace"):
    return AnnotatedSample(
        factuality_fail=fact, tone_fail=tone, error_types=tuple(types),
        severities=sev or {}, source=source,
    )


def _clean(source="devtrace"):
    return AnnotatedSample(False, False, (), {}, source)


def test_base_rate_prefers_anchor_pool() -> None:
    # Anchors: 1 of 4 fail -> 0.25, regardless of the noisier devtrace pool.
    samples = [
        _fail(["numeric_drift"], source="anchor"),
        _clean("anchor"), _clean("anchor"), _clean("anchor"),
        # devtrace pool is failure-heavy (selection bias) but must not set the rate.
        _fail(["numeric_drift"]), _fail(["entity_swap"]), _fail(["hype"], fact=False, tone=True),
    ]
    p = build_profile(samples)
    assert p.base_fail_rate_factuality == 0.25


def test_mix_is_normalized_and_floored() -> None:
    samples = [_fail(["numeric_drift"]) for _ in range(8)] + [_fail(["entity_swap"])]
    p = build_profile(samples, prob_floor=0.05)
    mix = p.composition_mix()
    assert abs(sum(mix.values()) - 1.0) < 1e-9
    assert all(v >= 0.05 - 1e-9 for v in mix.values())  # floor respected
    assert mix["numeric_drift"] > mix["entity_swap"]


def test_anchor_debias_shifts_mix() -> None:
    # devtrace says it's all numeric_drift; anchors reveal entity_swap is common.
    samples = [_fail(["numeric_drift"]) for _ in range(20)]
    samples += [_fail(["entity_swap"], source="anchor") for _ in range(5)]
    samples += [_fail(["numeric_drift"], source="anchor")]
    p = build_profile(samples)
    # Same samples with anchors relabelled as devtrace -> debiasing disabled, but
    # identical vocabulary, so the shares are comparable.
    no_debias = build_profile(
        [_fail(list(s.error_types), source="devtrace") for s in samples]
    )
    # Debiasing must pull entity_swap's share UP toward the unbiased anchors.
    assert p.composition_mix()["entity_swap"] > no_debias.composition_mix()["entity_swap"]


def test_severity_stats_and_sampling() -> None:
    samples = [_fail(["numeric_drift"], sev={"numeric_drift": 0.4}) for _ in range(10)]
    p = build_profile(samples)
    st = p.error_types["numeric_drift"]
    assert abs(st.severity_mean - 0.4) < 1e-9
    rng = random.Random(0)
    assert p.sample_operator(rng) == "numeric_drift"
    assert 0.0 <= p.sample_severity("numeric_drift", rng) <= 1.0


def test_empty_profile_is_safe() -> None:
    p = build_profile([_clean(), _clean()])
    assert p.composition_mix() == {}
    assert p.sample_operator(random.Random(0)) is None
    assert p.base_fail_rate_factuality == 0.0
