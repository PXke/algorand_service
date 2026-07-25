"""robots.txt gate: disallowed paths are blocked, allowed ones pass, and the guard fails open (no robots.txt / errors => allowed)."""

import pytest

from app.modules.crawler import robots


def test_disallow_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path matching robots.txt's Disallow rule is blocked; other paths remain allowed."""
    monkeypatch.setattr(robots, "CRAWLER_RESPECT_ROBOTS", True)
    monkeypatch.setattr(
        robots,
        "_robots_text",
        lambda _scheme, _netloc: "User-agent: *\nDisallow: /private",
    )
    assert robots.is_allowed("https://site.com/private/page") is False
    assert robots.is_allowed("https://site.com/public/page") is True


def test_no_robots_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing robots.txt fails open and allows the request."""
    monkeypatch.setattr(robots, "CRAWLER_RESPECT_ROBOTS", True)
    monkeypatch.setattr(robots, "_robots_text", lambda _scheme, _netloc: None)
    assert robots.is_allowed("https://site.com/anything") is True


def test_flag_off_allows_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """With CRAWLER_RESPECT_ROBOTS off, robots.txt is never even fetched and everything is allowed."""
    monkeypatch.setattr(robots, "CRAWLER_RESPECT_ROBOTS", False)
    # _robots_text must not even be consulted when disabled.
    monkeypatch.setattr(
        robots,
        "_robots_text",
        lambda _scheme, _netloc: "User-agent: *\nDisallow: /",
    )
    assert robots.is_allowed("https://site.com/private") is True


def test_malformed_url_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed URL fails open and is treated as allowed rather than raising."""
    monkeypatch.setattr(robots, "CRAWLER_RESPECT_ROBOTS", True)
    assert robots.is_allowed("not a url") is True
