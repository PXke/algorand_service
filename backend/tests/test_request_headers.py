"""client_ip() prefers X-Real-IP and the last X-Forwarded-For hop; session_token() falls back to the wallet_session cookie."""

from __future__ import annotations

from app.core.request_headers import client_ip, session_token


def test_session_token_prefers_the_header() -> None:
    """A real x-session-token header wins even when a (possibly stale) cookie is also present."""
    assert session_token({"x-session-token": "HDR", "Cookie": "wallet_session=COOKIE"}) == "HDR"


def test_session_token_falls_back_to_the_wallet_session_cookie() -> None:
    """No header at all -- 2026-09-07: this is what makes a cross-subdomain session work."""
    assert session_token({"Cookie": "wallet_session=COOKIE1"}) == "COOKIE1"


def test_session_token_reads_the_right_cookie_among_several() -> None:
    """wallet_session need not be the only or first cookie in the header."""
    assert session_token({"Cookie": "other=1; wallet_session=COOKIE2; another=3"}) == "COOKIE2"


def test_session_token_empty_without_header_or_cookie() -> None:
    """No auth signal at all -- empty string, same contract as before this fix."""
    assert session_token({}) == ""


def test_session_token_empty_header_falls_through_to_cookie() -> None:
    """An empty x-session-token value (e.g. a caller-side sentinel for 'no local token') must not shadow a real cookie."""
    assert session_token({"x-session-token": "", "Cookie": "wallet_session=COOKIE3"}) == "COOKIE3"


def test_client_ip_prefers_x_real_ip() -> None:
    """Nginx overwrites X-Real-IP from $remote_addr; it wins over a spoofed XFF."""
    assert (
        client_ip({"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "1.1.1.1, 203.0.113.9"})
        == "203.0.113.9"
    )


def test_client_ip_uses_last_forwarded_for_hop() -> None:
    """Without X-Real-IP, the last XFF hop is the one our proxy appended."""
    assert client_ip({"X-Forwarded-For": "1.1.1.1, 2.2.2.2, 10.0.0.5"}) == "10.0.0.5"


def test_client_ip_empty_without_headers() -> None:
    """No client address headers → empty string; callers decide fail-open vs closed."""
    assert client_ip({}) == ""
