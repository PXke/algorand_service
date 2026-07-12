"""fetch_url structures HTTP failures (401/403/429/5xx/404) instead of lumping
them into one opaque error string, so the writer can tell "blocked, try the
archive" apart from "gone" apart from "transient, maybe retry"."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai.research_tools import _tool_fetch_url


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Every retryable status/exception case here now runs through
    _guarded_get_with_retry's real 5-attempt backoff (added 2026-07-10) —
    without this, these tests genuinely sleep out the full schedule (80s for
    429, 30s for 500/503/timeouts), which is most of the whole suite's
    runtime for zero extra coverage. Retry COUNT/behavior is still exercised;
    only the wall-clock wait is removed."""
    monkeypatch.setattr("time.sleep", lambda _seconds: None)


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
def test_status_code_surfaces_targeted_hint(monkeypatch, status_code, expected_snippet):
    url = "https://example.com/article"
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get",
        lambda *a, **k: _status_response(url, status_code),
    )

    result = _tool_fetch_url(url)

    assert result["status_code"] == status_code
    assert expected_snippet in result["hint"]


def test_host_specific_hint_takes_priority_over_status_code(monkeypatch):
    url = "https://medium.com/@someone/a-post"
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get",
        lambda *a, **k: _status_response(url, 403),
    )

    result = _tool_fetch_url(url)

    assert result["status_code"] == 403
    assert "medium_api_article_list" in result["hint"]


def test_non_http_exception_has_no_status_code(monkeypatch):
    url = "https://example.com/article"

    def _boom(*a, **k):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr("app.modules.ai.research_tools._guarded_get", _boom)

    result = _tool_fetch_url(url)

    assert "status_code" not in result
    assert "error" in result
