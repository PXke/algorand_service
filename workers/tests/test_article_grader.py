"""Heuristic length/structure scoring bands for the article grader."""

from unittest.mock import patch

from app.core.config import LENGTH_OK_MAX_WORDS, LENGTH_OK_MIN_WORDS
from app.modules.newspaper.article_grader import (
    _length_score,
    _structure_score,
    composed_duplicates_latest_service_article,
)


def test_length_band_is_lax() -> None:
    """Word counts inside the OK band score 1.0; only extremes outside it ramp down."""
    # Anything inside [min, max] is full marks — length is not a target.
    assert _length_score(LENGTH_OK_MIN_WORDS) == 1.0
    assert _length_score((LENGTH_OK_MIN_WORDS + LENGTH_OK_MAX_WORDS) // 2) == 1.0
    assert _length_score(LENGTH_OK_MAX_WORDS) == 1.0
    # Outside the band ramps down (too short / bloated), but only at the extremes.
    assert _length_score(LENGTH_OK_MIN_WORDS // 2) < 1.0
    assert _length_score(LENGTH_OK_MAX_WORDS * 2) < 1.0


def test_structure_score_penalises_raw_text() -> None:
    """A wall of unstructured prose scores lower than text with headings, paragraphs, and a table."""
    raw = ("Algorand had a busy week across the ecosystem. " * 20).strip()
    structured = (
        "# Headline\n\nIntro paragraph with detail.\n\n"
        "| Metric | Value |\n| -- | -- |\n| TPS | 9400 |\n\n"
        "More analysis in a closing paragraph."
    )
    assert _structure_score(raw) < 0.5
    assert _structure_score(structured) > _structure_score(raw)


class _Prior:
    def __init__(self, title: str, summary: str, body: str) -> None:
        self.title = title
        self.summary = summary
        self.body = body


def test_composed_duplicates_latest_service_article_no_prior_article() -> None:
    """No prior article for the service -- never flagged, nothing to compare against."""
    with patch(
        "app.modules.newspaper.article_matching.find_latest_service_article", return_value=None
    ):
        is_dup, prior_id, score = composed_duplicates_latest_service_article(
            title="New Service Launches", summary="Summary.", body="Body.", service_id="brand-new"
        )
    assert is_dup is False
    assert prior_id == ""
    assert score == 0.0


def test_composed_duplicates_latest_service_article_same_facts_reworded() -> None:
    """Steak Pool regression pin (2026-08-02): a differently-worded draft reporting the SAME figures as the service's last article is flagged, even though the wording is not reused."""
    prior = _Prior(
        title="Algorand's Steak Pool burns 1.9M tokens via validator economics",
        summary="Steak Pool uses its 1.69% commission to buy and burn $STEAK, 11.16% of supply destroyed.",
        body="Réti validator #13 earns ALGO rewards; the burn address is provably unspendable.",
    )
    with (
        patch(
            "app.modules.newspaper.article_matching.find_latest_service_article",
            return_value="prior-id",
        ),
        patch("app.modules.newspaper.article_store.get_article", return_value=prior),
    ):
        is_dup, prior_id, score = composed_duplicates_latest_service_article(
            title="Algorand's Steak Pool burns 1.9M STEAK via validator commission",
            summary="Steak Pool ties Algorand staking yield to a deflationary ASA, "
            "using validator commissions to buy and burn 11.16% of supply on-chain.",
            body="Every block reward routes a 1.69% commission through Réti validator #13 "
            "into an immutable contract that burns STEAK to a provably unspendable address.",
            service_id="algostakepool-com",
        )
    assert is_dup is True
    assert prior_id == "prior-id"
    assert score >= 0.6


def test_composed_duplicates_latest_service_article_different_story_same_service() -> None:
    """A genuinely different update about the same service (different figures) is not flagged."""
    prior = _Prior(
        title="Steak Pool launches on Algorand",
        summary="A new deflationary staking project burns 500K STEAK at launch.",
        body="The validator commission is set to 1.69%, burning 5% of supply so far.",
    )
    with (
        patch(
            "app.modules.newspaper.article_matching.find_latest_service_article",
            return_value="prior-id",
        ),
        patch("app.modules.newspaper.article_store.get_article", return_value=prior),
    ):
        is_dup, _prior_id, score = composed_duplicates_latest_service_article(
            title="Steak Pool integrates with a new DEX for STEAK liquidity",
            summary="Steak Pool adds a Pact.fi pool with $2M in initial liquidity.",
            body="The integration routes 30% of trading fees into the existing burn mechanism, "
            "adding a second revenue stream alongside the 1.69% validator commission.",
            service_id="algostakepool-com",
        )
    assert is_dup is False
    assert score < 0.6


def test_composed_duplicates_latest_service_article_too_few_claims_not_flagged() -> None:
    """A draft with too few numeric claims to be meaningful evidence is never flagged, even at 100% overlap."""
    prior = _Prior(title="Steak Pool update", summary="Burns 5% of supply.", body="")
    with (
        patch(
            "app.modules.newspaper.article_matching.find_latest_service_article",
            return_value="prior-id",
        ),
        patch("app.modules.newspaper.article_store.get_article", return_value=prior),
    ):
        is_dup, _prior_id, _score = composed_duplicates_latest_service_article(
            title="Steak Pool grows",
            summary="Burns 5% of supply.",
            body="",
            service_id="algostakepool-com",
        )
    assert is_dup is False
