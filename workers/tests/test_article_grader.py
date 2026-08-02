"""Heuristic length/structure scoring bands for the article grader."""

from unittest.mock import patch

from app.core.config import LENGTH_OK_MAX_WORDS, LENGTH_OK_MIN_WORDS
from app.modules.newspaper.article_grader import (
    _length_score,
    _structure_score,
    composed_duplicates_latest_service_article,
    prior_service_article_summary,
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


def test_composed_duplicates_latest_service_article_same_pitch_different_numbers() -> None:
    """NFDomains regression pin (2026-08-02): a draft sharing almost no NUMBERS with its own prior coverage (a growing headline stat each time) but reusing the same explainer pitch/structure/vocabulary is flagged via body similarity, even though the numeric-overlap trigger alone would miss it."""
    prior = _Prior(
        title="NFDomains: Human-Readable Identities for Algorand's Decentralized Web",
        summary="NFDomains replaces cryptic wallet addresses with verifiable .algo names, "
        "enabling forward and reverse resolution on Algorand's Pure Proof-of-Stake chain.",
        body=(
            "Blockchain transactions rely on cryptographic addresses that are secure but "
            "unintuitive for users, prone to transcription errors and phishing risks. "
            "NFDomains provides a permissionless registry for .algo top-level domains, "
            "allowing users to mint unique, non-fungible identities as Algorand Standard "
            "Assets. Each NFD is a verifiable digital identity that maps a human-readable "
            "name to one or more blockchain addresses, metadata records, and social "
            "profiles. The system is built on Algorand's layer-1 infrastructure, "
            "leveraging Pure Proof-of-Stake consensus for fast finality and low fees. "
            "NFDomains enables two critical functions: forward and reverse resolution, "
            "converting a name into its associated address and back again."
        ),
    )
    with (
        patch(
            "app.modules.newspaper.article_matching.find_latest_service_article",
            return_value="prior-id",
        ),
        patch("app.modules.newspaper.article_store.get_article", return_value=prior),
    ):
        is_dup, prior_id, score = composed_duplicates_latest_service_article(
            title="Algorand's 69,000 Human-Readable Identities: How NFDomains Replaces "
            "Cryptic Addresses with .algo Names",
            summary="NFDomains' decentralized registry turns Algorand's 58-character "
            "addresses into portable .algo identities with vaults, verification, and a "
            "live marketplace-69,000+ names minted.",
            body=(
                "Algorand's 58-character addresses are secure but unusable for humans. "
                "Users memorize handles, not hex strings; every manual copy-paste risks a "
                "typo. NFDomains is Algorand's name service: a permissionless registry "
                "that turns opaque addresses into memorable .algo names. Each name is a "
                "non-fungible Algorand Standard Asset whose ownership is recorded "
                "on-chain, making .algo names portable digital identities. The platform's "
                "three core features -- minting, resolution, and vaults -- eliminate the "
                "friction of cryptic addresses. Forward resolution converts a name into "
                "an address plus linked metadata. Reverse resolution converts an address "
                "back into its registered name."
            ),
            service_id="nf-domains",
        )
    assert is_dup is True
    assert prior_id == "prior-id"
    assert score >= 0.22
    # And confirm the numeric-overlap trigger alone would NOT have caught this --
    # the two drafts share essentially no specific figures.
    from app.modules.gatekeeper.fact_align import numeric_entailment_score

    numeric = numeric_entailment_score(
        f"{prior.title}\n{prior.summary}\n{prior.body}",
        "Algorand's 69,000 Human-Readable Identities: How NFDomains Replaces Cryptic "
        "Addresses with .algo Names\nNFDomains' decentralized registry turns Algorand's "
        "58-character addresses into portable .algo identities with vaults, "
        "verification, and a live marketplace-69,000+ names minted.",
        tol=0.05,
    )
    assert numeric.total < 3 or numeric.score < 0.6


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


def test_prior_service_article_summary_names_the_prior_article() -> None:
    """Gives the writer the one fact abort_article(duplicate_coverage) needs: our own last article's title and summary."""
    prior = _Prior(
        title="NFDomains: Human-Readable Identities for Algorand's Decentralized Web",
        summary="NFDomains replaces cryptic wallet addresses with verifiable .algo names.",
        body="...",
    )
    with (
        patch(
            "app.modules.newspaper.article_matching.find_latest_service_article",
            return_value="prior-id",
        ),
        patch("app.modules.newspaper.article_store.get_article", return_value=prior),
    ):
        block = prior_service_article_summary("nf-domains")
    assert "NFDomains: Human-Readable Identities" in block
    assert "verifiable .algo names" in block
    assert "abort_article" in block  # points the writer at the actual tool name


def test_prior_service_article_summary_empty_when_no_prior_article() -> None:
    """No prior article -- an empty string, never a placeholder or error text."""
    with patch(
        "app.modules.newspaper.article_matching.find_latest_service_article",
        return_value=None,
    ):
        assert prior_service_article_summary("brand-new-service") == ""


def test_prior_service_article_summary_fails_open_on_store_error() -> None:
    """A Cassandra hiccup must never surface here -- empty string, same as no prior article."""
    with patch(
        "app.modules.newspaper.article_matching.find_latest_service_article",
        side_effect=RuntimeError("cassandra down"),
    ):
        assert prior_service_article_summary("nf-domains") == ""
