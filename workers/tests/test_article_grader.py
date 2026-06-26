from app.core.config import LENGTH_OK_MAX_WORDS, LENGTH_OK_MIN_WORDS
from app.modules.newspaper.article_grader import _length_score, _structure_score


def test_length_band_is_lax() -> None:
    # Anything inside [min, max] is full marks — length is not a target.
    assert _length_score(LENGTH_OK_MIN_WORDS) == 1.0
    assert _length_score((LENGTH_OK_MIN_WORDS + LENGTH_OK_MAX_WORDS) // 2) == 1.0
    assert _length_score(LENGTH_OK_MAX_WORDS) == 1.0
    # Outside the band ramps down (too short / bloated), but only at the extremes.
    assert _length_score(LENGTH_OK_MIN_WORDS // 2) < 1.0
    assert _length_score(LENGTH_OK_MAX_WORDS * 2) < 1.0


def test_structure_score_penalises_raw_text() -> None:
    raw = ("Algorand had a busy week across the ecosystem. " * 20).strip()
    structured = (
        "# Headline\n\nIntro paragraph with detail.\n\n"
        "- point one\n- point two\n\nMore analysis in a closing paragraph."
    )
    assert _structure_score(raw) < 0.5
    assert _structure_score(structured) > _structure_score(raw)
