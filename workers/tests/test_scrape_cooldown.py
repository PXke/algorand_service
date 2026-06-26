"""Exponential backoff schedule for the scrape cooldown (Reddit 429 storms)."""

from app.core import config
from app.core.net_guard import UnsafeUrlError
from app.modules.scraper.core.scrape_cooldown import (
    backoff_duration,
    cooldown_for_exception,
    is_permanent_failure,
)


def test_backoff_grows_exponentially() -> None:
    base = config.SCRAPE_BACKOFF_BASE_SECONDS
    mult = config.SCRAPE_BACKOFF_MULTIPLIER
    assert backoff_duration(1) == base
    assert backoff_duration(2) == int(base * mult)
    assert backoff_duration(3) == int(base * mult * mult)


def test_backoff_capped_and_floored() -> None:
    assert backoff_duration(99) == config.SCRAPE_BACKOFF_MAX_SECONDS
    # Zero/negative streaks never dip below the base.
    assert backoff_duration(0) == config.SCRAPE_BACKOFF_BASE_SECONDS
    assert backoff_duration(-5) == config.SCRAPE_BACKOFF_BASE_SECONDS


def test_dead_host_is_permanent_through_wrapping() -> None:
    # Scrapers wrap the SSRF guard's UnsafeUrlError; walk the cause chain.
    root = UnsafeUrlError("dns resolution failed for algoexplorer.io")
    wrapped = RuntimeError("playwright scrape failed: dns resolution failed")
    wrapped.__cause__ = root
    assert is_permanent_failure(wrapped)
    assert cooldown_for_exception(wrapped) == config.DEAD_HOST_COOLDOWN_SECONDS


def test_transient_failure_uses_exponential() -> None:
    transient = RuntimeError("403 Forbidden")
    assert not is_permanent_failure(transient)
    assert cooldown_for_exception(transient) is None
