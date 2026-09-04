"""Regression: domain_tracker.py's Redis-backed cooldown/budget checks fail.

Fail OPEN on a Redis error by design (CLAUDE.md section 2 item 9 -- a Redis
blip must not block crawl/compose), but every one of those sites must also
log a warning so a real outage is visible instead of silently masked. Before
this fix domain_tracker.py had no logger at all.
"""

from __future__ import annotations

import logging

import pytest

from app.modules.crawler import domain_tracker as dt


class _RaisingRedis:
    """Stand-in Redis client whose every attribute access raises."""

    def __getattr__(self, _name: str) -> object:
        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("redis down")

        return _boom


@pytest.fixture(autouse=True)
def _raising_redis_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every domain_tracker Redis call at a client that always raises."""
    monkeypatch.setattr(dt, "_crawl_budget_client", lambda: _RaisingRedis())


def test_record_domain_crawl_logs_on_redis_failure(caplog: pytest.LogCaptureFixture) -> None:
    """record_domain_crawl fails open to 0 but logs the Redis failure."""
    with caplog.at_level(logging.WARNING, logger="app.modules.crawler.domain_tracker"):
        assert dt.record_domain_crawl("example.com") == 0
    assert any("record_domain_crawl" in r.message for r in caplog.records)


def test_domain_crawl_count_logs_on_redis_failure(caplog: pytest.LogCaptureFixture) -> None:
    """domain_crawl_count fails open to 0 but logs the Redis failure."""
    with caplog.at_level(logging.WARNING, logger="app.modules.crawler.domain_tracker"):
        assert dt.domain_crawl_count("example.com") == 0
    assert any("domain_crawl_count" in r.message for r in caplog.records)


def test_record_domain_compose_logs_on_redis_failure(caplog: pytest.LogCaptureFixture) -> None:
    """record_domain_compose fails open to 0 but logs the Redis failure."""
    with caplog.at_level(logging.WARNING, logger="app.modules.crawler.domain_tracker"):
        assert dt.record_domain_compose("example.com") == 0
    assert any("record_domain_compose" in r.message for r in caplog.records)


def test_domain_in_cooldown_logs_on_redis_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """domain_in_cooldown fails open to False but logs the Redis failure."""
    monkeypatch.setattr("app.core.config.COMPOSE_DOMAIN_COOLDOWN_HOURS", 6)
    with caplog.at_level(logging.WARNING, logger="app.modules.crawler.domain_tracker"):
        assert dt.domain_in_cooldown("example.com") is False
    assert any("domain_in_cooldown" in r.message for r in caplog.records)


def test_record_service_compose_logs_on_redis_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """record_service_compose fails open (no-op) but logs the Redis failure."""
    monkeypatch.setattr("app.core.config.COMPOSE_SERVICE_COOLDOWN_HOURS", 6)
    with caplog.at_level(logging.WARNING, logger="app.modules.crawler.domain_tracker"):
        dt.record_service_compose("svc-1")
    assert any("record_service_compose" in r.message for r in caplog.records)


def test_service_in_cooldown_logs_on_redis_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """service_in_cooldown fails open to False but logs the Redis failure."""
    monkeypatch.setattr("app.core.config.COMPOSE_SERVICE_COOLDOWN_HOURS", 6)
    with caplog.at_level(logging.WARNING, logger="app.modules.crawler.domain_tracker"):
        assert dt.service_in_cooldown("svc-1") is False
    assert any("service_in_cooldown" in r.message for r in caplog.records)


def test_domain_compose_cap_reached_logs_on_redis_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """domain_compose_cap_reached fails open to False but logs the Redis failure."""
    with caplog.at_level(logging.WARNING, logger="app.modules.crawler.domain_tracker"):
        assert dt.domain_compose_cap_reached("example.com") is False
    assert any("domain_compose_cap_reached" in r.message for r in caplog.records)


def test_record_domain_auto_approved_logs_on_redis_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """record_domain_auto_approved fails open (no-op) but logs the Redis failure."""
    with caplog.at_level(logging.WARNING, logger="app.modules.crawler.domain_tracker"):
        dt.record_domain_auto_approved("example.com")
    assert any("record_domain_auto_approved" in r.message for r in caplog.records)


def test_url_recently_rejected_logs_on_redis_failure(caplog: pytest.LogCaptureFixture) -> None:
    """url_recently_rejected fails open to False but logs the Redis failure."""
    with caplog.at_level(logging.WARNING, logger="app.modules.crawler.domain_tracker"):
        assert dt.url_recently_rejected("https://example.com/x") is False
    assert any("url_recently_rejected" in r.message for r in caplog.records)
