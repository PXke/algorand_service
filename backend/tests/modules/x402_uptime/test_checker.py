"""checker.check_target: SSRF guard, redirect handling, error classification -- no real DNS/network.

Same convention `tests/test_media_routes.py` already uses:
`resolve_public_ip` is monkeypatched (it's the seam), and the transport is
`httpx.MockTransport`, which never opens a socket.
"""

from __future__ import annotations

import ssl

import httpx
import pytest

from app.modules.x402_uptime.services import checker


def test_reachable_ok_no_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain 200 is reported reachable, with the resolved IP and no error."""
    monkeypatch.setattr(checker, "resolve_public_ip", lambda _host: "203.0.113.5")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, headers={"Content-Type": "text/html"})

    result = checker.check_target(
        "https://example.com/",
        timeout_s=1.0,
        max_redirects=3,
        transport=httpx.MockTransport(handler),
    )

    assert result.reachable is True
    assert result.http_status == 200
    assert result.error == ""
    assert result.resolved_ip == "203.0.113.5"
    assert result.final_url == "https://example.com/"
    assert result.redirect_chain == ["https://example.com/"]
    assert result.response_time_ms >= 0


def test_redirect_chain_is_followed_and_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """One redirect hop is followed; both URLs land in redirect_chain and final_url is the last one."""
    monkeypatch.setattr(checker, "resolve_public_ip", lambda _host: "203.0.113.5")

    def handler(request: httpx.Request) -> httpx.Response:
        # The connect URL's host is the resolved IP, not the original hostname
        # (SSRF pinning) -- the original hostname only survives in the Host
        # header, which is what distinguishes hops here.
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return httpx.Response(200, headers={})

    result = checker.check_target(
        "https://example.com/",
        timeout_s=1.0,
        max_redirects=3,
        transport=httpx.MockTransport(handler),
    )

    assert result.reachable is True
    assert result.http_status == 200
    assert result.final_url == "https://example.com/final"
    assert result.redirect_chain == ["https://example.com/", "https://example.com/final"]


def test_too_many_redirects_reports_that_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirect chain longer than max_redirects gives up with error=too_many_redirects."""
    monkeypatch.setattr(checker, "resolve_public_ip", lambda _host: "203.0.113.5")
    hop_counter = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        # Always redirect to a new, distinct URL -- never converges.
        hop_counter["n"] += 1
        return httpx.Response(
            302, headers={"location": f"https://example.com/hop{hop_counter['n']}"}
        )

    result = checker.check_target(
        "https://example.com/",
        timeout_s=1.0,
        max_redirects=2,
        transport=httpx.MockTransport(handler),
    )

    assert result.reachable is False
    assert result.error == "too_many_redirects"
    assert len(result.redirect_chain) == 3  # original + 2 redirects


def test_redirect_never_bypasses_the_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public first hop redirecting to a private target must still be blocked -- the guard re-runs every hop."""

    def fake_resolve(host: str) -> str | None:
        return "203.0.113.5" if host == "example.com" else None

    monkeypatch.setattr(checker, "resolve_public_ip", fake_resolve)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        raise AssertionError("must never actually connect to the redirect target")

    result = checker.check_target(
        "https://example.com/",
        timeout_s=1.0,
        max_redirects=3,
        transport=httpx.MockTransport(handler),
    )

    assert result.reachable is False
    assert result.error == "non_public_target"


def test_non_public_target_is_distinguished_from_dns_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When DNS resolves but every address is private, the error is non_public_target, not dns_failure."""
    monkeypatch.setattr(checker, "resolve_public_ip", lambda _host: None)
    # DNS itself resolves fine -- resolve_public_ip returning None here means
    # every resolved address was private/reserved, not that DNS failed.
    monkeypatch.setattr(checker.socket, "getaddrinfo", lambda *_a, **_kw: [("dummy",)])

    result = checker.check_target(
        "https://internal.example/",
        timeout_s=1.0,
        max_redirects=3,
        transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
    )

    assert result.reachable is False
    assert result.error == "non_public_target"


def test_dns_failure_is_reported_distinctly(monkeypatch: pytest.MonkeyPatch) -> None:
    """When DNS itself never resolves, the error is dns_failure, not non_public_target."""
    monkeypatch.setattr(checker, "resolve_public_ip", lambda _host: None)

    def _raise(*_a: object, **_kw: object) -> None:
        raise OSError("name resolution failed")

    monkeypatch.setattr(checker.socket, "getaddrinfo", _raise)

    result = checker.check_target(
        "https://nowhere.invalid/",
        timeout_s=1.0,
        max_redirects=3,
        transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
    )

    assert result.reachable is False
    assert result.error == "dns_failure"


def test_connection_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain ConnectError (no TLS cause) is classified connection_refused."""
    monkeypatch.setattr(checker, "resolve_public_ip", lambda _host: "203.0.113.5")

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = checker.check_target(
        "https://example.com/",
        timeout_s=1.0,
        max_redirects=3,
        transport=httpx.MockTransport(handler),
    )

    assert result.reachable is False
    assert result.error == "connection_refused"


def test_tls_error_is_distinguished_from_plain_connection_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ConnectError caused by ssl.SSLError is classified tls_error, not connection_refused."""
    monkeypatch.setattr(checker, "resolve_public_ip", lambda _host: "203.0.113.5")

    def handler(_request: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLError("certificate verify failed")
        except ssl.SSLError as exc:
            raise httpx.ConnectError("ssl error") from exc

    result = checker.check_target(
        "https://example.com/",
        timeout_s=1.0,
        max_redirects=3,
        transport=httpx.MockTransport(handler),
    )

    assert result.reachable is False
    assert result.error == "tls_error"


def test_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ReadTimeout is classified timeout."""
    monkeypatch.setattr(checker, "resolve_public_ip", lambda _host: "203.0.113.5")

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    result = checker.check_target(
        "https://example.com/",
        timeout_s=1.0,
        max_redirects=3,
        transport=httpx.MockTransport(handler),
    )

    assert result.reachable is False
    assert result.error == "timeout"


def test_5xx_is_still_reachable_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 5xx means the target answered -- that is "reachable," even though it's a server error."""
    monkeypatch.setattr(checker, "resolve_public_ip", lambda _host: "203.0.113.5")

    result = checker.check_target(
        "https://example.com/",
        timeout_s=1.0,
        max_redirects=3,
        transport=httpx.MockTransport(lambda _r: httpx.Response(503)),
    )

    assert result.reachable is True
    assert result.http_status == 503
    assert result.error == ""
