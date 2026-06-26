"""Anchor pairing: human tags -> AnnotatedSample, and (human, machine) pair
construction feeding the validation harness."""

from app.modules.gatekeeper.anchors import build_pair, human_sample_from_tags
from app.modules.gatekeeper.validation import validate_annotator


def test_human_sample_from_tags() -> None:
    h = human_sample_from_tags(
        {"factuality_fail": True, "tone_fail": False, "error_types": ["numeric_drift"]}
    )
    assert h.factuality_fail and not h.tone_fail
    assert h.error_types == ("numeric_drift",)
    assert h.source == "anchor"


def test_human_sample_defaults() -> None:
    h = human_sample_from_tags({})
    assert not h.factuality_fail and not h.tone_fail and h.error_types == ()


def test_build_pair_runs_machine_annotator() -> None:
    # Human says factuality fail; Tier-1 should independently agree (ungrounded #).
    human, machine = build_pair(
        source_text="A routine update.",
        trace_text='{"tps": 50000}',
        article_text="It hit 50,000 TPS serving 999,000 users.",
        tags={"factuality_fail": True, "error_types": ["unsupported_elaboration"]},
    )
    assert human.factuality_fail and machine.factuality_fail
    assert "unsupported_elaboration" in machine.error_types


def test_pairs_feed_validation_harness() -> None:
    # End-to-end: a batch of anchor pairs runs through validate_annotator.
    pairs = []
    for _ in range(20):
        pairs.append(
            build_pair(
                source_text="A routine update.",
                trace_text='{"tps": 50000}',
                article_text="It hit 50,000 TPS serving 999,000 users.",
                tags={"factuality_fail": True, "error_types": ["unsupported_elaboration"]},
            )
        )
    report = validate_annotator(pairs)
    assert not report.gated
    # Tier-1 and the human agree on every anchor here.
    assert report.factuality_agreement == 1.0
    assert "unsupported_elaboration" in report.trusted_types
