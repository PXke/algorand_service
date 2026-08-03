"""telegram_channel_lookup writer tool (2026-08-03): a tool-gap suggestion ("A Telegram search would reveal whether the project has an official channel or active community discussion"). Bot API has no global search, so this is an honest handle-lookup + activity check via the platform's own already-configured distribution bot, not a search."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai import research_tools
from app.modules.ai.research_tools import _tool_telegram_channel_lookup
from app.modules.ai.research_tools import research_tools as research_tools_fn


def _json_response(url: str, status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


def test_existing_channel_reports_title_and_member_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real channel returns title/type/description plus its visible member count."""
    monkeypatch.setattr("app.core.config.TELEGRAM_BOT_TOKEN", "test-token")

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        if "getChatMemberCount" in url:
            return _json_response(url, 200, {"ok": True, "result": 1})
        if "getChat" in url:
            return _json_response(
                url,
                200,
                {
                    "ok": True,
                    "result": {
                        "title": "Algorand Name Service",
                        "type": "channel",
                        "description": "ANS",
                    },
                },
            )
        return _json_response(url, 404, {})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_telegram_channel_lookup("nfdomains")

    assert result["exists"] is True
    assert result["title"] == "Algorand Name Service"
    assert result["member_count"] == 1


def test_recent_post_date_extracted_from_public_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parses the most recent <time datetime> from the public t.me/s/<handle> preview as an activity signal."""
    monkeypatch.setattr("app.core.config.TELEGRAM_BOT_TOKEN", "test-token")

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        if "getChatMemberCount" in url:
            return _json_response(url, 200, {"ok": True, "result": 1})
        if "getChat" in url:
            return _json_response(
                url, 200, {"ok": True, "result": {"title": "X", "type": "channel"}}
            )
        if "t.me/s/" in url:
            html = (
                '<time datetime="2022-10-22T19:41:50+00:00">Oct 22</time>'
                '<time datetime="2021-01-01T00:00:00+00:00">Jan 1</time>'
            )
            return httpx.Response(200, text=html, request=httpx.Request("GET", url))
        return _json_response(url, 404, {})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_telegram_channel_lookup("nfdomains")

    assert result["most_recent_post_at"] == "2022-10-22T19:41:50+00:00"


def test_nonexistent_handle_reports_exists_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """A handle Telegram doesn't recognize is reported as not existing, not an error."""
    monkeypatch.setattr("app.core.config.TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **_kw: _json_response(
            url, 400, {"ok": False, "description": "Bad Request: chat not found"}
        ),
    )
    result = _tool_telegram_channel_lookup("definitely-not-a-real-handle-xyz")
    assert result == {"handle": "definitely-not-a-real-handle-xyz", "exists": False}


def test_strips_leading_at_sign(monkeypatch: pytest.MonkeyPatch) -> None:
    """A handle passed with a leading @ is normalized before use."""
    monkeypatch.setattr("app.core.config.TELEGRAM_BOT_TOKEN", "test-token")
    captured: list[str] = []

    def fake_get(url: str, **kw: object) -> httpx.Response:
        captured.append(str(kw.get("params")))
        return _json_response(url, 200, {"ok": True, "result": {}})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    _tool_telegram_channel_lookup("@nfdomains")

    assert any("'@nfdomains'" in c for c in captured)


def test_requires_nonempty_handle() -> None:
    """Rejects an empty handle with an error, no network call."""
    result = _tool_telegram_channel_lookup("")
    assert "error" in result


def test_not_configured_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns a clean error (not a crash) when TELEGRAM_BOT_TOKEN is unset."""
    monkeypatch.setattr("app.core.config.TELEGRAM_BOT_TOKEN", "")
    result = _tool_telegram_channel_lookup("nfdomains")
    assert result["error"] == "telegram lookup not configured"


def test_network_error_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network exception talking to the Bot API is caught, never raised."""
    monkeypatch.setattr("app.core.config.TELEGRAM_BOT_TOKEN", "test-token")

    def raise_error(_url: str, **_kw: object) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(research_tools, "_guarded_get", raise_error)
    result = _tool_telegram_channel_lookup("nfdomains")
    assert "error" in result


def test_registered_only_when_token_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool only shows up in the registry when TELEGRAM_BOT_TOKEN is set."""
    monkeypatch.setattr("app.core.config.TELEGRAM_BOT_TOKEN", "")
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "telegram_channel_lookup" not in names
    assert "telegram_channel_lookup" not in handlers

    monkeypatch.setattr("app.core.config.TELEGRAM_BOT_TOKEN", "test-token")
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "telegram_channel_lookup" in names
    assert "telegram_channel_lookup" in handlers
