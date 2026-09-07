"""app.core.net_guard.guarded_get: SSRF-guarded GET, now served off the process-cached shared client (app.core.http_client.get_http_client) instead of a fresh httpx.Client() per call -- these tests cover both that guarded_get's own redirect-revalidation behavior survived the refactor, and that it actually goes through the shared-client seam (not a lingering direct httpx.Client() construction)."""

from __future__ import annotations

import httpx
import pytest

from app.core.net_guard import ResponseTooLargeError, UnsafeUrlError, guarded_get, guarded_request


def _patch_shared_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> list:
    """Route get_http_client() to a MockTransport-backed real httpx.Client (no real sockets), and record every (kwargs) it was called with."""
    import app.core.http_client as http_client_module

    calls: list[dict] = []
    client = httpx.Client(transport=transport)

    def _fake_get_http_client(**kwargs: object) -> httpx.Client:
        calls.append(kwargs)
        return client

    monkeypatch.setattr(http_client_module, "get_http_client", _fake_get_http_client)
    return calls


def test_guarded_get_follows_a_same_process_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 302 to another public host is followed once, re-validated, and the final response returned."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/start":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return httpx.Response(200, json={"ok": True})

    calls = _patch_shared_client(monkeypatch, httpx.MockTransport(handler))

    resp = guarded_get("https://example.com/start")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # get_http_client is called through the shared-client seam, not a bare
    # httpx.Client() construction -- and only once per guarded_get call
    # (the client itself, not the redirect loop, owns re-use).
    assert len(calls) == 1
    assert calls[0]["follow_redirects"] is False


def test_guarded_get_rejects_a_redirect_to_a_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirect that lands on a private/internal IP is rejected even though the first hop was public -- the whole reason follow_redirects stays off and hops are re-validated manually."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/start":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data"}
            )
        raise AssertionError("must not actually request the internal redirect target")

    _patch_shared_client(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(UnsafeUrlError):
        guarded_get("https://example.com/start")


def test_guarded_get_passes_through_timeout_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Timeout is forwarded to the shared-client seam and headers/params reach the actual request."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["query"] = str(request.url)
        return httpx.Response(200, json={})

    calls = _patch_shared_client(monkeypatch, httpx.MockTransport(handler))

    guarded_get(
        "https://example.com/data",
        headers={"X-Test": "yes"},
        params={"q": "algorand"},
        timeout=7.5,
    )

    assert calls[0]["timeout"] == 7.5
    assert seen["headers"]["x-test"] == "yes"
    assert "q=algorand" in seen["query"]


def test_guarded_request_head_rejects_a_redirect_to_a_private_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HEAD uses the same hop re-validation as GET — domain_probe must not follow a 302 to metadata."""
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if str(request.url) == "https://example.com/start":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/"})
        raise AssertionError("must not actually request the internal redirect target")

    _patch_shared_client(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(UnsafeUrlError):
        guarded_request("HEAD", "https://example.com/start")
    assert methods == ["HEAD"]


# --------------------------------------------------------------------------- #
# Response-body cap (2026-09-07 security review, finding 6): guarded_get/
# guarded_request back ~110 call sites across the crawler, scrapers, and
# writer/investigative tools, including free unauthenticated fetches of a
# caller-supplied URL -- with no cap, a hostile or misconfigured target could
# be buffered into memory in full, one OOM'd worker per request. Enforced
# against bytes actually streamed, never a declared (spoofable/absent)
# Content-Length.
# --------------------------------------------------------------------------- #
def test_guarded_get_raises_when_the_body_exceeds_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A response whose real body exceeds max_bytes is aborted mid-stream, not buffered in full."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000)

    _patch_shared_client(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(ResponseTooLargeError):
        guarded_get("https://example.com/huge", max_bytes=1000)


def test_guarded_get_returns_a_usable_response_under_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response under the cap is returned as a normal, fully-usable Response -- .text/.json()/.url all still work."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    _patch_shared_client(monkeypatch, httpx.MockTransport(handler))

    resp = guarded_get("https://example.com/small", max_bytes=1000)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert str(resp.url) == "https://example.com/small"


def test_guarded_get_defaults_the_cap_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no explicit max_bytes, the module-level NET_GUARD_MAX_RESPONSE_BYTES setting is what gets enforced."""
    import app.core.net_guard as net_guard_module

    monkeypatch.setattr(net_guard_module, "NET_GUARD_MAX_RESPONSE_BYTES", 100)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 500)

    _patch_shared_client(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(ResponseTooLargeError):
        guarded_get("https://example.com/big")


def test_a_redirect_hops_body_is_never_read_or_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The redirect leg's own (potentially huge) body must never be read at all -- only its status/Location header matter -- so it cannot trip the cap regardless of size."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/start":
            # A redirect response carrying a body far past the cap -- must
            # never be read, so this alone must not raise.
            return httpx.Response(
                302,
                headers={"location": "https://example.com/final"},
                content=b"x" * 5000,
            )
        return httpx.Response(200, json={"ok": True})

    _patch_shared_client(monkeypatch, httpx.MockTransport(handler))

    resp = guarded_get("https://example.com/start", max_bytes=100)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
