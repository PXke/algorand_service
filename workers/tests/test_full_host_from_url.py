"""full_host_from_url: the exact-host counterpart to domain_from_url's eTLD+1 collapse -- see its own docstring for why service_sources.venue_owner_for_url needs both."""

import pytest

from app.modules.crawler.domain_tracker import domain_from_url, full_host_from_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://forum.algorand.co/latest", "forum.algorand.co"),
        ("https://www.algorand.co/x", "www.algorand.co"),
        ("https://xgov.algorand.co", "xgov.algorand.co"),
        ("https://algorand.co", "algorand.co"),
        ("not-a-url", ""),
        ("", ""),
        # Same SPA-engine-prefix and bare-hostname normalization as
        # domain_from_url (shared helper) -- just without the eTLD+1 fold.
        ("browser://https://forum.algorand.co/latest", "forum.algorand.co"),
        ("lora.algokit.io", "lora.algokit.io"),
    ],
)
def test_full_host_from_url(url: str, expected: str) -> None:
    """Resolves each vector to its EXACT host, never collapsed to a registrable eTLD+1."""
    assert full_host_from_url(url) == expected


def test_full_host_from_url_never_collapses_a_subdomain_unlike_domain_from_url() -> None:
    """The whole reason this function exists: forum.algorand.co is its own deliberately-carved-out venue (algorand-forum), distinct from algorand.co's own site -- domain_from_url's generic eTLD+1 heuristic has no way to know that and collapses it away, so venue_owner_for_url needs the UNCOLLAPSED host to find the right reverse-index claim."""
    url = "https://forum.algorand.co/latest"
    assert domain_from_url(url) == "algorand.co"
    assert full_host_from_url(url) == "forum.algorand.co"
    assert full_host_from_url(url) != domain_from_url(url)
