"""fetch_domain_preview logging — routine frontier failures stay quiet."""

from app.core.net_guard import UnsafeUrlError
from app.modules.scraper.core.link_extractor import _expected_preview_failure


def test_dns_failure_is_expected_preview_failure() -> None:
    exc = UnsafeUrlError("dns resolution failed for app.dappflow.org")
    assert _expected_preview_failure(exc) is True


def test_unexpected_parse_error_is_not_expected() -> None:
    assert _expected_preview_failure(ValueError("unexpected parser bug")) is False
