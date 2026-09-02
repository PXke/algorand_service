"""client_ip() prefers X-Real-IP and the last X-Forwarded-For hop."""

from __future__ import annotations

from app.core.request_headers import client_ip


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
