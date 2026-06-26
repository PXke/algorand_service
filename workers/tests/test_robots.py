"""robots.txt gate: disallowed paths are blocked, allowed ones pass, and the
guard fails open (no robots.txt / errors => allowed)."""

from app.modules.crawler import robots


def test_disallow_blocks(monkeypatch):
    monkeypatch.setattr(robots, "CRAWLER_RESPECT_ROBOTS", True)
    monkeypatch.setattr(
        robots, "_robots_text",
        lambda scheme, netloc: "User-agent: *\nDisallow: /private",
    )
    assert robots.is_allowed("https://site.com/private/page") is False
    assert robots.is_allowed("https://site.com/public/page") is True


def test_no_robots_allows(monkeypatch):
    monkeypatch.setattr(robots, "CRAWLER_RESPECT_ROBOTS", True)
    monkeypatch.setattr(robots, "_robots_text", lambda scheme, netloc: None)
    assert robots.is_allowed("https://site.com/anything") is True


def test_flag_off_allows_everything(monkeypatch):
    monkeypatch.setattr(robots, "CRAWLER_RESPECT_ROBOTS", False)
    # _robots_text must not even be consulted when disabled.
    monkeypatch.setattr(
        robots, "_robots_text",
        lambda scheme, netloc: "User-agent: *\nDisallow: /",
    )
    assert robots.is_allowed("https://site.com/private") is True


def test_malformed_url_allowed(monkeypatch):
    monkeypatch.setattr(robots, "CRAWLER_RESPECT_ROBOTS", True)
    assert robots.is_allowed("not a url") is True
