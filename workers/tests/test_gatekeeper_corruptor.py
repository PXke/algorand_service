"""Corruptor mutations: anchor-aware drift with a tolerance band, hard negatives, and the symmetric-paraphraser discipline that prevents fingerprint shortcuts."""

import random

from app.modules.gatekeeper import corruptor as cor
from app.modules.gatekeeper.corruptor import Sample


def _gold() -> Sample:
    return Sample(
        source="Algorand shipped an upgrade.",
        trace='{"tps": 50000, "supply": 10000000000}',
        article="The network hit 50,000 TPS on a 10000000000 supply.",
        label_factuality=1.0,
        label_tone=1.0,
        operator="gold",
        severity=0.0,
    )


def test_numeric_drift_changes_a_grounded_number() -> None:
    """Mutates a grounded number, tagging the sample as numeric_drift with a valid factuality label."""
    rng = random.Random(1)
    out = cor.numeric_drift(_gold(), rng, max_pct=0.5)
    assert out is not None
    assert out.article != _gold().article
    assert out.operator == "numeric_drift"
    assert 0.0 <= out.label_factuality <= 1.0


def test_numeric_drift_within_tolerance_stays_positive() -> None:
    """Keeps the factuality label at 1.0 (hard positive) when the drift falls inside the tolerance band."""
    # Force a tiny drift by seeding until pct <= TOLERANCE, then assert label 1.0.
    found_small = False
    for seed in range(200):
        out = cor.numeric_drift(_gold(), random.Random(seed), max_pct=0.5)
        if out and out.severity == 0.0:
            assert out.label_factuality == 1.0  # hard positive
            found_small = True
            break
    assert found_small


def test_numeric_drift_none_without_grounded_number() -> None:
    """Returns None when the article has no grounded number to drift."""
    g = Sample(
        source="s",
        trace="{}",
        article="No numbers here.",
        label_factuality=1.0,
        label_tone=1.0,
        operator="gold",
        severity=0.0,
    )
    assert cor.numeric_drift(g, random.Random(0)) is None


def test_cross_contamination_inserts_donor_value() -> None:
    """Inserts an unrelated donor trace's value into the article and marks it factuality 0."""
    rng = random.Random(3)
    out = cor.cross_contamination(_gold(), donor_trace='{"other": 777777}', rng=rng)
    assert out is not None
    assert out.label_factuality == 0.0
    assert "777,777" in out.article or "777777" in out.article


def test_symmetric_positive_uses_paraphraser_but_stays_clean() -> None:
    """Runs the article through a paraphraser yet keeps both factuality and tone labels positive."""
    para = lambda s: s + " (rewritten)"  # noqa: E731
    out = cor.symmetric_positive(_gold(), para)
    assert out.label_factuality == 1.0
    assert out.label_tone == 1.0
    assert "(rewritten)" in out.article  # LLM-touched, yet a positive


def test_temporal_collapse_is_factuality_negative() -> None:
    """Fabricates a false "just launched" chronology, marking factuality 0 while tone stays 1.0."""
    para = lambda s: "BREAKING — just launched this week: " + s  # noqa: E731
    out = cor.temporal_collapse(_gold(), para, random.Random(0))
    assert out.label_factuality == 0.0  # fabricated chronology
    assert out.label_tone == 1.0  # facts/tone unchanged
    assert out.operator == "temporal_collapse"
    assert "just launched" in out.article


def test_hype_rewrite_graded_label() -> None:
    """Gives a higher-intensity hype rewrite a lower tone score than a milder one."""
    para = lambda s: "MASSIVE explosive gains " + s  # noqa: E731
    mild = cor.hype_rewrite(_gold(), para, random.Random(0), intensity=0.2)
    strong = cor.hype_rewrite(_gold(), para, random.Random(0), intensity=0.9)
    assert mild.label_tone > strong.label_tone  # higher intensity -> lower tone score
