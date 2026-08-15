"""Opportunistic real-world project-scale signal (DeFiLlama TVL, or GitHub stars).

Feeds compute_priority's scale_pts term and Lane 2 ("biggest/most
significant") of the publish-queue lane split.

Distinct from service_profile.py's score_service_impressiveness(), which only
measures page-content *shape* (length, headings, doc-links) — this measures
actual scale. See workers/tests/test_service_scale.py.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Unresolved (no identifier guessable, or every lookup failed/404'd) is
# deliberately NOT the same as "resolved and genuinely tiny" -- see
# resolve_service_scale's docstring. A wallet app or governance forum with no
# TVL/GitHub concept at all must not be punished relative to a DeFi/OSS
# project that merely *has* a measurable signal.
UNRESOLVED_SCALE = 0.45
UNRESOLVED_SOURCE = "none"

_TVL_BUCKETS: list[tuple[float, float]] = [
    (10_000.0, 0.05),
    (100_000.0, 0.15),
    (1_000_000.0, 0.35),
    (10_000_000.0, 0.55),
    (50_000_000.0, 0.75),
    (200_000_000.0, 0.90),
]
_TVL_MAX = 1.00

_STAR_BUCKETS: list[tuple[float, float]] = [
    (10.0, 0.15),
    (50.0, 0.30),
    (200.0, 0.50),
    (1_000.0, 0.65),
    (5_000.0, 0.80),
]
# Stars are a noisier proxy than TVL (a wallet-UI repo collects stars for
# reasons unrelated to protocol significance) -- capped below TVL's ceiling.
_STAR_MAX = 0.85


def _bucket_tvl_usd(tvl_usd: float) -> float:
    for ceiling, score in _TVL_BUCKETS:
        if tvl_usd < ceiling:
            return score
    return _TVL_MAX


def _bucket_github_stars(stars: float) -> float:
    for ceiling, score in _STAR_BUCKETS:
        if stars < ceiling:
            return score
    return _STAR_MAX


def _guess_defillama_slugs(display_name: str, source_url: str) -> list[str]:
    """Candidate DeFiLlama slugs, most likely first.

    The display name, then the domain's registrable label (e.g.
    "https://tinyman.org" -> "tinyman") as a fallback when the display name
    doesn't match DeFiLlama's naming.
    """
    from urllib.parse import urlparse

    slugs: list[str] = []
    name_slug = (display_name or "").strip().lower().replace(" ", "-")
    if name_slug:
        slugs.append(name_slug)
    host = (urlparse(source_url or "").hostname or "").lower()
    host = host.removeprefix("www.")
    domain_slug = host.split(".")[0] if host else ""
    if domain_slug and domain_slug not in slugs:
        slugs.append(domain_slug)
    return slugs


def _fetch_defillama_tvl(slug: str) -> float | None:
    """One DeFiLlama /tvl/{slug} lookup.

    None on 404/error (not "unresolved floor" -- the caller may still have
    another slug or GitHub to try).
    """
    from app.modules.ai.research_tools import _guarded_get

    try:
        resp = _guarded_get(f"https://api.llama.fi/tvl/{slug}", timeout=9.0)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        tvl_usd = resp.json()
    except Exception:
        logger.debug("defillama TVL lookup failed for slug=%s", slug, exc_info=True)
        return None
    return float(tvl_usd) if isinstance(tvl_usd, (int, float)) else None


def _resolve_via_defillama(display_name: str, source_url: str) -> tuple[float, str] | None:
    """Try DeFiLlama TVL across candidate slugs.

    Returns None (not the "unresolved" floor) if every candidate
    404s/errors, so the caller can fall through to GitHub.
    """
    for slug in _guess_defillama_slugs(display_name, source_url):
        tvl_usd = _fetch_defillama_tvl(slug)
        if tvl_usd is not None:
            return _bucket_tvl_usd(tvl_usd), "defillama_tvl"
    return None


def _resolve_via_github(outbound_links: list[str]) -> tuple[float, str] | None:
    """Try GitHub org stars from the first github.com link found.

    Returns None (not the unresolved floor) on any failure so the caller
    floors.
    """
    from app.modules.ai.research_tools import _GITHUB_REPO_URL_RE, _github_owner_total_stars

    owner: str | None = None
    for link in outbound_links or []:
        m = _GITHUB_REPO_URL_RE.search(link or "")
        if m:
            owner = m.group(1)
            break
    if not owner:
        return None
    try:
        total_stars, _complete = _github_owner_total_stars(owner, None)
    except Exception:
        logger.debug("github stars lookup failed for owner=%s", owner, exc_info=True)
        return None
    if total_stars is None:
        return None
    return _bucket_github_stars(float(total_stars)), "github_stars"


def resolve_service_scale(
    *, display_name: str, source_url: str = "", outbound_links: list[str] | None = None
) -> tuple[float, str]:
    """Opportunistically resolve a real-world scale signal for one service.

    DeFiLlama TVL first (cheap, one call), then GitHub org stars from
    outbound_links if TVL didn't resolve. Returns (score 0.0-1.0, source).

    Critical correctness property: "unresolved" (no identifier could be
    guessed, or every lookup errored/404'd) must NOT collapse to the same
    score as "resolved and genuinely small" -- only a successful numeric read
    feeds the stepped buckets; failure floors at UNRESOLVED_SCALE so services
    with no TVL/GitHub concept at all aren't punished relative to ones that
    merely have a measurable signal, while a real small TVL/star-count still
    scores low. Getting this backwards would reward opacity over verifiable
    smallness.
    """
    via_tvl = _resolve_via_defillama(display_name, source_url)
    if via_tvl is not None:
        return via_tvl
    via_github = _resolve_via_github(outbound_links or [])
    if via_github is not None:
        return via_github
    return UNRESOLVED_SCALE, UNRESOLVED_SOURCE
