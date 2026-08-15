"""play_interactive keeps ONE page open across open/click/type/read actions, unlike fetch_url/click_element/type_into_page (each a fresh page every call) -- for discovering a live app/game's mechanics across a short sequence of steps, not mastering or completing it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.ai.research_tools import _tool_play_interactive
from app.modules.scraper.core.browser_scrape import BrowserScrapeError


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.closed = False

    def _result(self, engine: str) -> SimpleNamespace:
        return SimpleNamespace(final_url="https://example.com/state", title="State", text="page text", engine=engine)

    def interactive_open(self, url: str) -> SimpleNamespace:
        self.calls.append(("open", url))
        return self._result("playwright-interactive")

    def interactive_click(self, target: str) -> SimpleNamespace:
        self.calls.append(("click", target))
        return self._result("playwright-interactive-click")

    def interactive_type(self, target: str, value: str, *, submit: bool = False) -> SimpleNamespace:
        self.calls.append(("type", target, value, submit))
        return self._result("playwright-interactive-type")

    def interactive_read(self) -> SimpleNamespace:
        self.calls.append(("read",))
        return self._result("playwright-interactive-read")

    def interactive_close(self) -> None:
        self.calls.append(("close",))
        self.closed = True


def test_play_interactive_requires_a_browser_session() -> None:
    """No playwright_session means no browser is available -- fail clearly rather than silently no-op."""
    result = _tool_play_interactive("open", url="https://example.com")
    assert "error" in result
    assert "session" in result["error"]


def test_play_interactive_open_requires_url() -> None:
    """An empty url on action='open' is a usage error, not a navigation attempt."""
    session = _FakeSession()
    result = _tool_play_interactive("open", playwright_session=session)
    assert "error" in result
    assert session.calls == []


def test_play_interactive_open_prepends_https_when_scheme_missing() -> None:
    """A bare domain (no scheme) is upgraded to https:// before opening."""
    session = _FakeSession()
    _tool_play_interactive("open", url="lumirogue.com", playwright_session=session)
    assert session.calls == [("open", "https://lumirogue.com")]


def test_play_interactive_click_requires_target() -> None:
    """An empty target on action='click' is a usage error."""
    session = _FakeSession()
    result = _tool_play_interactive("click", playwright_session=session)
    assert "error" in result
    assert session.calls == []


def test_play_interactive_type_requires_target() -> None:
    """An empty target on action='type' is a usage error."""
    session = _FakeSession()
    result = _tool_play_interactive("type", value="hello", playwright_session=session)
    assert "error" in result
    assert session.calls == []


def test_play_interactive_type_passes_value_and_submit() -> None:
    """Value and submit are forwarded to the session's interactive_type call."""
    session = _FakeSession()
    _tool_play_interactive("type", target="Search", value="Ankh", submit=True, playwright_session=session)
    assert session.calls == [("type", "Search", "Ankh", True)]


def test_play_interactive_read_needs_no_extra_args() -> None:
    """action='read' re-reads the current page state with no extra arguments."""
    session = _FakeSession()
    result = _tool_play_interactive("read", playwright_session=session)
    assert result["action"] == "read"
    assert session.calls == [("read",)]


def test_play_interactive_close_calls_session_and_returns_status() -> None:
    """action='close' ends the interactive session and reports it, rather than reading page state."""
    session = _FakeSession()
    result = _tool_play_interactive("close", playwright_session=session)
    assert result == {"action": "close", "status": "closed"}
    assert session.closed is True


def test_play_interactive_rejects_unknown_action() -> None:
    """An unrecognized action is a usage error, never dispatched to the session."""
    session = _FakeSession()
    result = _tool_play_interactive("dance", playwright_session=session)
    assert "error" in result
    assert session.calls == []


def test_play_interactive_surfaces_session_errors(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ARG001
    """A real interaction failure (e.g. no matching click target) surfaces as a clean error, not a crash."""

    class _FailingSession(_FakeSession):
        def interactive_click(self, target: str) -> SimpleNamespace:
            raise BrowserScrapeError(f"no element matching {target!r}")

    result = _tool_play_interactive("click", target="Nonexistent", playwright_session=_FailingSession())
    assert "error" in result


def test_play_interactive_returns_shaped_result() -> None:
    """The public result shape mirrors fetch_url/click_element: action, url, title, text."""
    session = _FakeSession()
    result = _tool_play_interactive("open", url="https://example.com", playwright_session=session)
    assert result["action"] == "open"
    assert result["url"] == "https://example.com/state"
    assert result["title"] == "State"
    assert result["text"] == "page text"


def test_play_interactive_tool_registered() -> None:
    """Registers play_interactive in both the tool schemas and handlers."""
    from app.modules.ai.research_tools import research_tools

    schemas, handlers = research_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "play_interactive" in names
    assert "play_interactive" in handlers
