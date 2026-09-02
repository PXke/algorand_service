"""domain_probe must re-validate redirect hops (SSRF)."""

from __future__ import annotations

import httpx
import pytest

from app.modules.newspaper.writer_enrichment.collectors import domain_probe


def test_probe_domain_rejects_a_redirect_to_a_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public origin that 302s to metadata/loopback must not be followed; the probe errors, it does not leak internal headers."""
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        raise AssertionError("must not actually request the internal redirect target")

    import app.core.http_client as http_client_module

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(http_client_module, "get_http_client", lambda **_k: client)

    result = domain_probe.probe_domain("example.com")

    assert result["https"] is False
    assert "error" in result
    assert methods == ["HEAD"]
    assert result.get("headers") is None
    assert result["safety_hint"] == "unreachable_or_tls_issue"


def test_probe_domain_head_then_get_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """HEAD 405 falls back to a hop-checked GET, still never auto-follows redirects."""
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(
            200,
            headers={"strict-transport-security": "max-age=31536000"},
        )

    import app.core.http_client as http_client_module

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(http_client_module, "get_http_client", lambda **_k: client)

    result = domain_probe.probe_domain("example.com")

    assert methods == ["HEAD", "GET"]
    assert result["status_code"] == 200
    assert result["hsts"] is True
    assert result["safety_hint"] == "https_with_hsts"


def test_probe_domain_loopback_first_hop_never_requests() -> None:
    """A non-public first hop is rejected before any HTTP client call."""
    result = domain_probe.probe_domain("127.0.0.1")

    assert result["https"] is False
    assert result["safety_hint"] == "unreachable_or_tls_issue"
    assert "error" in result
