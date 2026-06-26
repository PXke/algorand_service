"""Annotator: Tier-1 deterministic numeric grounding, Tier-2 LLM union (injected,
no network), defensive parsing, and the unclassified/novel-mode canary."""

from app.modules.gatekeeper import annotator as an
from app.modules.gatekeeper.profile import AnnotatedSample


def test_tier1_flags_ungrounded_numbers() -> None:
    t1 = an.tier1_annotate(
        source_text="A routine update.",
        trace_text='{"tps": 50000}',
        article_text="It hit 50,000 TPS serving 999,000 users.",
        fact_min=0.8,
    )
    assert t1.factuality_fail
    assert "unsupported_elaboration" in t1.error_types
    assert "999,000" in t1.ungrounded
    assert 0.0 < t1.severities["unsupported_elaboration"] <= 1.0


def test_tier1_clean_when_grounded() -> None:
    t1 = an.tier1_annotate("s", '{"tps": 50000}', "Hit 50,000 TPS.", fact_min=0.8)
    assert not t1.factuality_fail
    assert t1.error_types == ()


def test_tier2_coerce_filters_unknown_types() -> None:
    raw = {"factuality_fail": True, "tone_fail": True,
           "error_types": ["entity_swap", "made_up_type"],
           "severities": {"entity_swap": 0.9, "made_up_type": 1.0},
           "confidence": 0.8}
    t2 = an._coerce_tier2(raw)
    assert t2.error_types == ("entity_swap",)        # unknown dropped
    assert t2.unclassified                            # ...and canary flipped
    assert t2.severities == {"entity_swap": 0.9}


def test_tier2_severity_clamped_and_defaulted() -> None:
    t2 = an._coerce_tier2({"error_types": ["hype"], "severities": {"hype": 5.0}})
    assert t2.severities["hype"] == 1.0               # clamped to [0,1]
    assert t2.tone_fail                                # inferred from a tone type


def test_annotate_unions_tiers() -> None:
    # Tier-1 finds the numeric problem; Tier-2 adds a tone problem.
    def classify(s, tr, a):
        return {"tone_fail": True, "error_types": ["hype"], "severities": {"hype": 0.6}}

    sample = an.annotate(
        source_text="A routine update.",
        trace_text='{"tps": 50000}',
        article_text="MASSIVE: it hit 50,000 TPS serving 999,000 users!",
        classify=classify,
    )
    assert isinstance(sample, AnnotatedSample)
    assert sample.factuality_fail and sample.tone_fail
    assert set(sample.error_types) == {"unsupported_elaboration", "hype"}


def test_annotate_degrades_to_tier1_when_classifier_raises() -> None:
    def broken(s, tr, a):
        raise RuntimeError("LLM down")

    sample = an.annotate(
        "s", '{"tps": 50000}', "Hit 50,000 TPS serving 999,000 users.",
        classify=broken,
    )
    assert sample.factuality_fail              # Tier-1 still works
    assert not sample.tone_fail                # Tier-2 contributed nothing
    assert sample.error_types == ("unsupported_elaboration",)


def test_annotate_feeds_build_profile() -> None:
    # End-to-end: annotator output is directly consumable by build_profile.
    from app.modules.gatekeeper.profile import build_profile

    samples = [
        an.annotate("s", '{"x": 1}', "claimed 999 widgets", fact_min=0.8)
        for _ in range(5)
    ]
    prof = build_profile(samples)
    assert "unsupported_elaboration" in prof.composition_mix()
    assert prof.base_fail_rate_factuality == 1.0
