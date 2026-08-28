"""Exponential backoff schedule for the scrape cooldown (Reddit 429 storms)."""

from typing import Never

import pytest
from conftest import FakeRedis

from app.core import config
from app.core.net_guard import UnsafeUrlError
from app.modules.scraper.core import scrape_cooldown
from app.modules.scraper.core.scrape_cooldown import (
    backoff_duration,
    cooldown_for_exception,
    is_on_cooldown,
    is_permanent_failure,
    record_scrape_failure,
)


def test_backoff_grows_exponentially() -> None:
    """Backoff duration grows exponentially with the failure streak."""
    base = config.SCRAPE_BACKOFF_BASE_SECONDS
    mult = config.SCRAPE_BACKOFF_MULTIPLIER
    assert backoff_duration(1) == base
    assert backoff_duration(2) == int(base * mult)
    assert backoff_duration(3) == int(base * mult * mult)


def test_backoff_capped_and_floored() -> None:
    """Backoff is capped at the configured max and floored at the base for non-positive streaks."""
    assert backoff_duration(99) == config.SCRAPE_BACKOFF_MAX_SECONDS
    # Zero/negative streaks never dip below the base.
    assert backoff_duration(0) == config.SCRAPE_BACKOFF_BASE_SECONDS
    assert backoff_duration(-5) == config.SCRAPE_BACKOFF_BASE_SECONDS


def test_dead_host_is_permanent_through_wrapping() -> None:
    # Scrapers wrap the SSRF guard's UnsafeUrlError; walk the cause chain.
    """A wrapped DNS-failure cause is still recognized as a permanent failure with the dead-host cooldown."""
    root = UnsafeUrlError("dns resolution failed for algoexplorer.io")
    wrapped = RuntimeError("playwright scrape failed: dns resolution failed")
    wrapped.__cause__ = root
    assert is_permanent_failure(wrapped)
    assert cooldown_for_exception(wrapped) == config.DEAD_HOST_COOLDOWN_SECONDS


def test_transient_failure_uses_exponential() -> None:
    """A generic transient failure is not permanent and has no fixed cooldown."""
    transient = RuntimeError("403 Forbidden")
    assert not is_permanent_failure(transient)
    assert cooldown_for_exception(transient) is None


def test_is_on_cooldown_false_before_any_failure(patch_redis_from_url: FakeRedis) -> None:  # noqa: ARG001 -- name must match the real callee's keyword arg
    """A service with no recorded failure is not on cooldown."""
    assert is_on_cooldown("some-service") == (False, "")


def test_is_on_cooldown_true_after_recorded_failure(patch_redis_from_url: FakeRedis) -> None:  # noqa: ARG001 -- name must match the real callee's keyword arg
    """A freshly recorded failure puts the service on cooldown with a reason tag."""
    record_scrape_failure("some-service")
    on_cooldown, reason = is_on_cooldown("some-service")
    assert on_cooldown is True
    assert reason.startswith("cooldown_until_")


def test_is_on_cooldown_fails_open_on_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis error must never crash the scrape beat -- is_on_cooldown fails open (not on cooldown), like its scrape_throttled/mark_scraped siblings, and logs a warning instead of raising."""

    def _boom() -> Never:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(scrape_cooldown, "_client", _boom)

    logged: list[str] = []
    monkeypatch.setattr(
        scrape_cooldown.logger, "warning", lambda msg, *args, **_kw: logged.append(msg % args)
    )

    assert is_on_cooldown("some-service") == (False, "")
    assert logged
    assert "some-service" in logged[0]
