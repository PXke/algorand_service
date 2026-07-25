"""Drift triggers: min-n gating must suppress firing on thin data; each test fires only on a genuine shift."""

from app.modules.gatekeeper import drift


def test_min_n_gates_suppress_thin_windows() -> None:
    """Gates every drift detector when the sample count is below its min-n threshold."""
    assert drift.cusum_base_rate([1, 0, 1], 0.05, 0.10).gated
    assert drift.psi({"a": 1.0}, {"a": 1.0}, n_failures=3).gated
    assert drift.ece([0.5] * 10, [0.5] * 10).gated
    assert drift.novel_mode(1, 10).gated


def test_cusum_fires_on_sustained_shift() -> None:
    """Fires the CUSUM detector once the observed failure rate sustains well above the prior."""
    # True rate 0.30 vs deployed-prior 0.05 -> CUSUM should cross h.
    flags = ([1] * 30 + [0] * 70) * 2  # 200 samples, ~30% fail
    r = drift.cusum_base_rate(flags, p0=0.05, p1=0.10)
    assert not r.gated
    assert r.fired
    assert r.action == "hot_update_c"


def test_cusum_quiet_when_stable() -> None:
    """Stays quiet when the observed failure rate matches the prior."""
    flags = ([0] * 19 + [1]) * 10  # 200 samples, ~5% fail == p0
    r = drift.cusum_base_rate(flags, p0=0.05, p1=0.10)
    assert not r.gated
    assert not r.fired


def test_psi_fires_on_composition_shift() -> None:
    """Fires the PSI detector when the failure-mode mix shifts away from the deployed profile."""
    profile = {"numeric_drift": 0.6, "entity_swap": 0.3, "hype": 0.1}
    current = {"numeric_drift": 0.1, "entity_swap": 0.2, "hype": 0.7}
    r = drift.psi(current, profile, n_failures=80)
    assert not r.gated
    assert r.fired
    assert r.statistic > 0.25


def test_psi_stable_when_matched() -> None:
    """Stays quiet when the current failure-mode mix matches the deployed profile."""
    mix = {"numeric_drift": 0.6, "entity_swap": 0.3, "hype": 0.1}
    r = drift.psi(dict(mix), dict(mix), n_failures=80)
    assert not r.gated
    assert not r.fired


def test_ece_fires_on_miscalibration() -> None:
    """Fires the ECE detector when confident predictions are consistently wrong."""
    # Confident (0.9) but always wrong (label 0.0) -> large ECE.
    probs = [0.9] * 600
    labels = [0.0] * 600
    r = drift.ece(probs, labels)
    assert not r.gated
    assert r.fired
    assert r.statistic > 0.05


def test_ece_quiet_when_calibrated() -> None:
    """Stays quiet when predicted probabilities are well calibrated."""
    # Half at 0.0 correct, half at 1.0 correct -> ECE ~ 0.
    probs = [0.0] * 300 + [1.0] * 300
    labels = [0.0] * 300 + [1.0] * 300
    r = drift.ece(probs, labels)
    assert not r.gated
    assert not r.fired


def test_novel_mode_fires_above_floor() -> None:
    """Fires when the unclassified-sample rate exceeds the novel-mode floor, but not below it."""
    r = drift.novel_mode(n_unclassified=40, n_total=300)  # ~13%
    assert not r.gated
    assert r.fired
    r2 = drift.novel_mode(n_unclassified=3, n_total=300)  # 1%
    assert not r2.gated
    assert not r2.fired
