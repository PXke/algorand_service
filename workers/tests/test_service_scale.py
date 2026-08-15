"""service_scale.py: bucket boundaries and the unresolved-vs-resolved-tiny non-collapse property the whole design depends on."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.modules.newspaper.service_scale import (
    UNRESOLVED_SCALE,
    UNRESOLVED_SOURCE,
    _bucket_github_stars,
    _bucket_tvl_usd,
    resolve_service_scale,
)


def test_bucket_tvl_usd_boundaries() -> None:
    """Each TVL bucket boundary lands on the expected score, at each edge."""
    assert _bucket_tvl_usd(0) == 0.05
    assert _bucket_tvl_usd(9_999) == 0.05
    assert _bucket_tvl_usd(10_000) == 0.15
    assert _bucket_tvl_usd(99_999) == 0.15
    assert _bucket_tvl_usd(100_000) == 0.35
    assert _bucket_tvl_usd(999_999) == 0.35
    assert _bucket_tvl_usd(1_000_000) == 0.55
    assert _bucket_tvl_usd(9_999_999) == 0.55
    assert _bucket_tvl_usd(10_000_000) == 0.75
    assert _bucket_tvl_usd(49_999_999) == 0.75
    assert _bucket_tvl_usd(50_000_000) == 0.90
    assert _bucket_tvl_usd(199_999_999) == 0.90
    assert _bucket_tvl_usd(200_000_000) == 1.00
    assert _bucket_tvl_usd(10_000_000_000) == 1.00


def test_bucket_github_stars_boundaries() -> None:
    """Each GitHub-star bucket boundary lands on the expected score, at each edge."""
    assert _bucket_github_stars(0) == 0.15
    assert _bucket_github_stars(9) == 0.15
    assert _bucket_github_stars(10) == 0.30
    assert _bucket_github_stars(49) == 0.30
    assert _bucket_github_stars(50) == 0.50
    assert _bucket_github_stars(199) == 0.50
    assert _bucket_github_stars(200) == 0.65
    assert _bucket_github_stars(999) == 0.65
    assert _bucket_github_stars(1_000) == 0.80
    assert _bucket_github_stars(4_999) == 0.80
    assert _bucket_github_stars(5_000) == 0.85
    assert _bucket_github_stars(1_000_000) == 0.85


def test_stars_ceiling_stays_below_tvl_ceiling() -> None:
    """Stars are a noisier proxy -- even a huge star count must never reach TVL's 1.00 ceiling."""
    assert _bucket_github_stars(10_000_000) < _bucket_tvl_usd(1_000_000_000)


def _mock_response(status_code: int = 200, json_value: object = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_value
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.side_effect = None
    return resp


def test_resolve_unresolved_when_defillama_404s_and_no_github_link() -> None:
    """No resolvable identifier at all -- must land on the unresolved floor, not 0.0."""
    with patch(
        "app.modules.ai.research_tools._guarded_get",
        return_value=_mock_response(status_code=404),
    ):
        score, source = resolve_service_scale(
            display_name="Totally Unknown Project",
            source_url="https://totally-unknown-project.example",
            outbound_links=[],
        )
    assert score == UNRESOLVED_SCALE
    assert source == UNRESOLVED_SOURCE


def test_resolve_unresolved_on_network_error() -> None:
    """A network error resolving TVL, with no GitHub fallback available, also floors -- never raises out of ingest."""
    with patch(
        "app.modules.ai.research_tools._guarded_get",
        side_effect=ConnectionError("boom"),
    ):
        score, source = resolve_service_scale(
            display_name="Some Protocol",
            source_url="https://some-protocol.example",
            outbound_links=[],
        )
    assert score == UNRESOLVED_SCALE
    assert source == UNRESOLVED_SOURCE


def test_resolve_via_defillama_small_real_tvl_scores_low_not_floor() -> None:
    """A genuinely small, RESOLVED TVL must score low, not collapse to the unresolved floor -- the core correctness property."""
    with patch(
        "app.modules.ai.research_tools._guarded_get",
        return_value=_mock_response(status_code=200, json_value=500.0),
    ):
        score, source = resolve_service_scale(
            display_name="Tiny Protocol",
            source_url="https://tiny-protocol.example",
            outbound_links=[],
        )
    assert source == "defillama_tvl"
    assert score == _bucket_tvl_usd(500.0)
    assert score < UNRESOLVED_SCALE


def test_resolve_via_defillama_large_tvl() -> None:
    """A large, resolved TVL scores well above the unresolved floor."""
    with patch(
        "app.modules.ai.research_tools._guarded_get",
        return_value=_mock_response(status_code=200, json_value=25_000_000.0),
    ):
        score, source = resolve_service_scale(
            display_name="Big Protocol",
            source_url="https://big-protocol.example",
            outbound_links=[],
        )
    assert source == "defillama_tvl"
    assert score == _bucket_tvl_usd(25_000_000.0)
    assert score > UNRESOLVED_SCALE


def test_resolve_falls_back_to_github_when_tvl_unresolved() -> None:
    """A 404 on DeFiLlama falls through to GitHub stars when a repo link is available."""
    with (
        patch(
            "app.modules.ai.research_tools._guarded_get",
            return_value=_mock_response(status_code=404),
        ),
        patch(
            "app.modules.ai.research_tools._github_owner_total_stars",
            return_value=(1500, True),
        ),
    ):
        score, source = resolve_service_scale(
            display_name="Wallet App With No TVL",
            source_url="https://wallet-app.example",
            outbound_links=["https://github.com/wallet-org/wallet-app"],
        )
    assert source == "github_stars"
    assert score == _bucket_github_stars(1500)
