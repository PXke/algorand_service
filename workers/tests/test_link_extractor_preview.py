"""fetch_domain_preview logging — routine frontier failures stay quiet."""

from app.core.net_guard import UnsafeUrlError
from app.modules.scraper.core.link_extractor import _expected_preview_failure


def test_dns_failure_is_expected_preview_failure() -> None:
    """A DNS-resolution failure is an expected, quiet preview failure."""
    exc = UnsafeUrlError("dns resolution failed for app.dappflow.org")
    assert _expected_preview_failure(exc) is True


def test_unexpected_parse_error_is_not_expected() -> None:
    """An unexpected parser exception is not treated as an expected preview failure."""
    assert _expected_preview_failure(ValueError("unexpected parser bug")) is False
