"""Stream-B estimators with the survivorship-bias firewall.

The governing rule: only the uniform audit stream may estimate the absolute base
rate. Every estimator that feeds a prior asserts ``selected_by == "uniform"`` and
refuses any row lacking sampling provenance — making the bias trap structurally
impossible, not merely a convention. Composition may pool uniform + stratified
via inverse-probability weighting, but is barred from the base rate.

Rows are plain mappings (as read from ``gatekeeper_audit``); kept torch-free and
unit-tested.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

AuditRow = Mapping[str, object]


class FirewallError(ValueError):
    """Raised when a row without valid sampling provenance reaches an estimator."""


def _require_uniform(rows: Iterable[AuditRow]) -> list[AuditRow]:
    out: list[AuditRow] = []
    for r in rows:
        sel = r.get("selected_by")
        if sel is None:
            raise FirewallError("audit row missing selected_by; refusing to estimate")
        if sel == "uniform":
            out.append(r)
    return out


def estimate_base_fail_rate(
    rows: Iterable[AuditRow], *, threshold: float = 0.5
) -> dict[str, float]:
    """P(failure) from UNIFORM rows only. A row is a failure when its annotator
    soft label crosses ``threshold`` (failure == low quality). Stratified rows
    are silently excluded; provenance-less rows raise."""
    uni = _require_uniform(rows)
    n = len(uni)
    if n == 0:
        return {"base_fail_rate_factuality": 0.0, "base_fail_rate_tone": 0.0, "n": 0}
    fact_fail = sum(1 for r in uni if float(r.get("label_factuality", 1.0)) < threshold)
    tone_fail = sum(1 for r in uni if float(r.get("label_tone", 1.0)) < threshold)
    return {
        "base_fail_rate_factuality": fact_fail / n,
        "base_fail_rate_tone": tone_fail / n,
        "n": n,
    }


def estimate_composition(
    rows: Iterable[AuditRow], *, threshold: float = 0.5
) -> dict[str, float]:
    """Error-type mix among FAILURES, pooled across uniform + stratified rows via
    inverse-probability weighting. This may use stratified rows (it needs the
    rare failures) — but it never touches the base rate. Returns a normalized
    histogram over taxonomy tags."""
    weighted: dict[str, float] = {}
    total = 0.0
    for r in rows:
        if r.get("selected_by") is None:
            raise FirewallError("audit row missing selected_by; refusing to estimate")
        is_fail = (float(r.get("label_factuality", 1.0)) < threshold
                   or float(r.get("label_tone", 1.0)) < threshold)
        if not is_fail:
            continue
        prob = float(r.get("selection_prob", 1.0)) or 1.0
        w = 1.0 / prob  # inverse-probability weight
        for tag in (r.get("error_types") or []):  # type: ignore[union-attr]
            weighted[str(tag)] = weighted.get(str(tag), 0.0) + w
            total += w
    if total <= 0:
        return {}
    return {k: v / total for k, v in weighted.items()}
