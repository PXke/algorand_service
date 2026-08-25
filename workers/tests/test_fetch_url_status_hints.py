"""fetch_url structures HTTP failures (401/403/429/5xx/404) instead of lumping them into one opaque error string, so the writer can tell "blocked, try the archive" apart from "gone" apart from "transient, maybe retry"."""

from __future__ import annotations

from typing import Never

import httpx
import pytest

from app.modules.ai.research_tools import _tool_fetch_url


def _status_response(url: str, status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=httpx.Request("GET", url))


@pytest.mark.parametrize(
    ("status_code", "expected_snippet"),
    [
        (401, "login-walled or bot-blocked"),
        (403, "login-walled or bot-blocked"),
        (429, "rate-limited"),
        (500, "likely transient"),
        (503, "likely transient"),
        (404, "page is gone"),
        (410, "page is gone"),
    ],
)
def test_status_code_surfaces_targeted_hint(
    monkeypatch: pytest.MonkeyPatch, status_code: int, expected_snippet: str
) -> None:
    """Each HTTP status code surfaces its own targeted hint (blocked, rate-limited, transient, or gone)."""
    url = "https://example.com/article"
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get",
        lambda *_a, **_k: _status_response(url, status_code),
    )

    result = _tool_fetch_url(url)

    assert result["status_code"] == status_code
    assert expected_snippet in result["hint"]


def test_host_specific_hint_takes_priority_over_status_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host-specific hint takes priority over the generic status-code hint."""
    url = "https://medium.com/@someone/a-post"
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get",
        lambda *_a, **_k: _status_response(url, 403),
    )

    result = _tool_fetch_url(url)

    assert result["status_code"] == 403
    assert "medium_api_article_list" in result["hint"]


def test_non_http_exception_has_no_status_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-HTTP exception (e.g. connect timeout) has no status_code in the result, only an error."""
    url = "https://example.com/article"

    def _boom(*_a: object, **_k: object) -> Never:
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr("app.modules.ai.research_tools._guarded_get", _boom)

    result = _tool_fetch_url(url)

    assert "status_code" not in result
    assert "error" in result


def test_dns_failure_flags_domain_as_defunct(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-resolving domain (the MyAlgo incident, 2026-07-19) must produce a strong 'defunct — do not recommend' hint, not just a terse error the writer can ignore."""
    from app.core.net_guard import UnsafeUrlError

    url = "https://myalgo.com/"

    def _boom(*_a: object, **_k: object) -> Never:
        raise UnsafeUrlError("dns resolution failed for myalgo.com")

    monkeypatch.setattr("app.modules.ai.research_tools._guarded_get", _boom)

    result = _tool_fetch_url(url)

    assert "status_code" not in result
    hint = result.get("hint", "")
    assert "DEFUNCT" in hint
    assert "do not link" in hint.lower()
