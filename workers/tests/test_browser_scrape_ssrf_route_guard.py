"""_deny_private_requests (browser_scrape.py) -- Playwright route-level SSRF guard.

Regression coverage for the 2026-09-07 security review, finding 5:
net_guard.assert_public_url's own pre-goto() check only ever covered the
STARTING navigation URL passed to fetch_page/click_and_read -- Chromium
itself then followed any server-side redirect and loaded every subresource
(scripts, images, XHR/fetch, iframes) with no further validation, so a
crawled page could redirect the headless browser into an internal service
(gunicorn, Typesense, the cloud metadata endpoint) and whatever that
service returned would be extracted and stored as scraped source material.

_deny_private_requests is registered context-wide (`context.route("**/*",
...)`) on both contexts this module creates (PlaywrightSession's main
context and capture_screenshot's own), so it intercepts every request --
main navigation, redirect hop, and subresource alike -- not just the first.
These tests exercise the handler itself against a fake Playwright `route`
object; a real browser/context is neither available nor needed to prove
its allow/deny logic.
"""

from __future__ import annotations

import pytest

from app.modules.scraper.core.browser_scrape import _deny_private_requests


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class _FakeRoute:
    """Records whether continue_()/abort() was called, never both."""

    def __init__(self, url: str) -> None:
        self.request = _FakeRequest(url)
        self.continued = False
        self.aborted = False

    def continue_(self) -> None:
        self.continued = True

    def abort(self) -> None:
        self.aborted = True


@pytest.fixture(autouse=True)
def _no_real_dns_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """assert_public_url resolves DNS for real -- stub the underlying resolver, not assert_public_url itself, so the guard's own scheme/IP logic still runs."""

    def _fake_getaddrinfo(host: str, *_a: object, **_kw: object) -> list:
        ip = "93.184.216.34" if host == "public.example.com" else "127.0.0.1"
        return [(None, None, None, None, (ip, 0))]

    monkeypatch.setattr("app.core.net_guard.socket.getaddrinfo", _fake_getaddrinfo)


def test_a_public_https_request_is_continued() -> None:
    """A normal public-host request passes through untouched."""
    route = _FakeRoute("https://public.example.com/script.js")

    _deny_private_requests(route)

    assert route.continued is True
    assert route.aborted is False


def test_a_request_to_a_literal_private_ip_is_aborted() -> None:
    """A subresource/redirect that targets a private IP directly is blocked, not just DNS-resolved hostnames."""
    route = _FakeRoute("http://127.0.0.1:9080/api/v1/admin/wallets")

    _deny_private_requests(route)

    assert route.aborted is True
    assert route.continued is False


def test_a_request_to_the_cloud_metadata_endpoint_is_aborted() -> None:
    """The literal-IP case the whole guard exists for: 169.254.169.254."""
    route = _FakeRoute("http://169.254.169.254/latest/meta-data/")

    _deny_private_requests(route)

    assert route.aborted is True
    assert route.continued is False


def test_a_request_that_resolves_to_a_private_ip_is_aborted() -> None:
    """A hostname whose DNS answer lands on a private IP is blocked, not just a literal private-IP URL."""
    route = _FakeRoute("http://internal.example.com/secret")

    _deny_private_requests(route)

    assert route.aborted is True
    assert route.continued is False


@pytest.mark.parametrize(
    "url", ["data:image/png;base64,AAAA", "blob:https://x/1234", "about:blank"]
)
def test_non_network_schemes_are_never_checked_or_blocked(url: str) -> None:
    """data:/blob:/about: never leave the browser process -- nothing for assert_public_url to check, and they must never be aborted."""
    route = _FakeRoute(url)

    _deny_private_requests(route)

    assert route.continued is True
    assert route.aborted is False
