"""Deciding which scrape engine a domain/scheme needs."""

import pytest

from app.core import config
from app.modules.scraper.core.scrape_engine import uses_browser_engine


def test_browser_scheme_always_uses_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Always routes a browser:// scheme URL to the browser engine."""
    monkeypatch.setattr(config, "SCRAPE_ENGINE_DEFAULT", "auto")
    assert uses_browser_engine("browser://https://app.example.com/dashboard")


def test_allowlisted_https_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routes an https domain in BROWSER_SCRAPE_DOMAINS to the browser engine."""
    monkeypatch.setattr(config, "SCRAPE_ENGINE_DEFAULT", "auto")
    monkeypatch.setattr(config, "BROWSER_SCRAPE_DOMAINS", "app.example.com")
    assert uses_browser_engine("https://app.example.com/roadmap")


def test_plain_http_not_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Does not route a non-allowlisted https domain to the browser engine."""
    monkeypatch.setattr(config, "SCRAPE_ENGINE_DEFAULT", "auto")
    monkeypatch.setattr(config, "BROWSER_SCRAPE_DOMAINS", "discord.com")
    assert not uses_browser_engine("https://example.com/docs")


def test_discord_scheme_uses_dedicated_scraper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routes a discord:// scheme URL away from the browser engine to its dedicated scraper."""
    monkeypatch.setattr(config, "SCRAPE_ENGINE_DEFAULT", "auto")
    assert not uses_browser_engine("discord://channels/1/2")


def test_scrape_engine_default_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routes any plain https URL to the browser engine when it is the configured default."""
    monkeypatch.setattr(config, "SCRAPE_ENGINE_DEFAULT", "browser")
    assert uses_browser_engine("https://news.example.com/post/1")
