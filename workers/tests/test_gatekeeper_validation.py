"""Validation harness: per-type precision/recall, the min-anchors/min-support gates, and the trust filter that keeps untrusted machine labels out of Layer 1."""

from app.modules.gatekeeper.profile import AnnotatedSample
from app.modules.gatekeeper.validation import (
    apply_trust,
    validate_annotator,
)


def _s(types: tuple[str, ...] = (), fact: bool = False, tone: bool = False) -> AnnotatedSample:
    return AnnotatedSample(fact, tone, tuple(types), dict.fromkeys(types, 0.5))


def test_gated_below_min_anchors() -> None:
    """Gates validation and trusts nothing when there are too few anchor pairs."""
    pairs = [(_s(["hype"], tone=True), _s(["hype"], tone=True)) for _ in range(5)]
    r = validate_annotator(pairs)
    assert r.gated
    assert r.trusted_types == frozenset()  # trust nothing on thin data


def test_type_trusted_when_annotator_agrees() -> None:
    """Trusts an error type once the annotator matches human labels with perfect precision/recall over enough support."""
    # 25 anchors; annotator nails entity_swap on all 10 positives, no false pos.
    pairs = [(_s(["entity_swap"], fact=True), _s(["entity_swap"], fact=True)) for _ in range(10)]
    pairs.extend((_s(), _s()) for _ in range(15))
    r = validate_annotator(pairs)
    assert not r.gated
    m = r.per_type["entity_swap"]
    assert m.precision == 1.0
    assert m.recall == 1.0
    assert m.support == 10
    assert "entity_swap" in r.trusted_types


def test_type_untrusted_on_low_precision() -> None:
    """Withholds trust from a type the annotator over-calls, driving precision below the threshold."""
    # Annotator over-calls hype: 6 true, but also 10 false positives -> precision 0.375.
    pairs = [(_s(["hype"], tone=True), _s(["hype"], tone=True)) for _ in range(6)]
    pairs.extend((_s(), _s(["hype"], tone=True)) for _ in range(10))  # false positives
    pairs.extend((_s(), _s()) for _ in range(9))
    r = validate_annotator(pairs)
    m = r.per_type["hype"]
    assert m.precision < 0.8
    assert "hype" not in r.trusted_types


def test_type_untrusted_on_low_support() -> None:
    """Withholds trust from a type with too few human-labeled positives, even at perfect precision/recall."""
    # Only 2 human positives for cross_contamination (< MIN_SUPPORT) even at 100% p/r.
    pairs = [
        (_s(["cross_contamination"], fact=True), _s(["cross_contamination"], fact=True))
        for _ in range(2)
    ]
    pairs += [(_s(), _s()) for _ in range(20)]
    r = validate_annotator(pairs)
    assert r.per_type["cross_contamination"].support == 2
    assert "cross_contamination" not in r.trusted_types


def test_fail_flag_agreement() -> None:
    """Computes factuality-flag agreement rate as the fraction of matching human/annotator labels."""
    pairs = [(_s(fact=True), _s(fact=True)) for _ in range(15)]
    pairs += [(_s(fact=True), _s(fact=False)) for _ in range(5)]  # 5 disagreements
    r = validate_annotator(pairs)
    assert abs(r.factuality_agreement - 0.75) < 1e-9


def test_apply_trust_drops_untrusted_types() -> None:
    """Drops error types and severities not in the trusted set while keeping the fail flags intact."""
    sample = AnnotatedSample(
        factuality_fail=True,
        tone_fail=True,
        error_types=("unsupported_elaboration", "clickbait"),
        severities={"unsupported_elaboration": 0.9, "clickbait": 0.5},
    )
    out = apply_trust(sample, frozenset({"unsupported_elaboration"}))
    assert out.error_types == ("unsupported_elaboration",)
    assert "clickbait" not in out.severities
    assert out.factuality_fail
    assert out.tone_fail


def test_summary_serializable() -> None:
    """Serializes the validation result to a dict with anchor count and per-type stats."""
    pairs = [(_s(["hype"], tone=True), _s(["hype"], tone=True)) for _ in range(20)]
    s = validate_annotator(pairs).summary()
    assert s["n_anchors"] == 20
    assert "hype" in s["per_type"]
