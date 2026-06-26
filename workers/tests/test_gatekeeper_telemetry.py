"""Stream-B firewall: base-rate estimation rejects provenance-less rows, ignores
stratified rows, and composition pools via IPW."""

import pytest

from app.modules.gatekeeper import telemetry as t


def _row(sel, lf=1.0, lt=1.0, prob=1.0, types=()):
    return {"selected_by": sel, "label_factuality": lf, "label_tone": lt,
            "selection_prob": prob, "error_types": list(types)}


def test_base_rate_uses_uniform_only() -> None:
    rows = [
        _row("uniform", lf=0.1),   # factuality failure
        _row("uniform", lf=0.9),
        _row("uniform", lf=0.9),
        _row("uniform", lf=0.9),
        _row("stratified", lf=0.0),  # must be ignored despite being a failure
    ]
    out = t.estimate_base_fail_rate(rows)
    assert out["n"] == 4
    assert out["base_fail_rate_factuality"] == 0.25  # 1/4, stratified excluded


def test_firewall_rejects_missing_provenance() -> None:
    bad = [{"label_factuality": 0.1}]  # no selected_by
    with pytest.raises(t.FirewallError):
        t.estimate_base_fail_rate(bad)


def test_composition_ipw_pools_streams() -> None:
    rows = [
        _row("uniform", lf=0.1, prob=1.0, types=("numeric_drift",)),
        # stratified failure sampled at 10% -> weight 10, dominates the mix
        _row("stratified", lf=0.1, prob=0.1, types=("entity_swap",)),
    ]
    mix = t.estimate_composition(rows)
    assert mix["entity_swap"] > mix["numeric_drift"]
    assert abs(sum(mix.values()) - 1.0) < 1e-9


def test_composition_ignores_clean_rows() -> None:
    rows = [_row("uniform", lf=1.0, lt=1.0, types=("numeric_drift",))]
    assert t.estimate_composition(rows) == {}
