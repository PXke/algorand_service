"""_wrap_play_interactive enforces PLAY_INTERACTIVE_MAX_STEPS per compose -- exploring a system's mechanics is meant to take a handful of steps, not become an open-ended playthrough."""

from __future__ import annotations

import pytest

from app.modules.ai import writer_tools as wt


def _make_handler(recorder: list) -> object:
    def handler(**kwargs: object) -> dict:
        recorder.append(kwargs)
        action = kwargs.get("action")
        if action == "close":
            return {"action": "close", "status": "closed"}
        return {"action": action, "text": "ok"}

    return handler


def test_wrap_play_interactive_injects_session_from_context() -> None:
    """The persistent PlaywrightSession under _playwright_session in context reaches the handler."""
    recorder: list = []
    ctx = {"_playwright_session": "the-session"}
    wrapped = wt._wrap_play_interactive(_make_handler(recorder), ctx)

    wrapped(action="open", url="https://example.com")

    assert recorder[0]["playwright_session"] == "the-session"


def test_wrap_play_interactive_counts_successful_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each successful open/click/type/read call increments the shared step counter."""
    monkeypatch.setattr("app.core.config.PLAY_INTERACTIVE_MAX_STEPS", 3)
    recorder: list = []
    ctx: dict = {}
    wrapped = wt._wrap_play_interactive(_make_handler(recorder), ctx)

    r1 = wrapped(action="open", url="https://example.com")
    r2 = wrapped(action="click", target="Start")

    assert r1["budget"] == {"used": 1, "max": 3}
    assert r2["budget"] == {"used": 2, "max": 3}


def test_wrap_play_interactive_refuses_once_budget_is_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A step beyond the budget is refused before it ever reaches the handler."""
    monkeypatch.setattr("app.core.config.PLAY_INTERACTIVE_MAX_STEPS", 2)
    recorder: list = []
    ctx: dict = {}
    wrapped = wt._wrap_play_interactive(_make_handler(recorder), ctx)

    wrapped(action="open", url="https://example.com")
    wrapped(action="click", target="Start")
    result = wrapped(action="click", target="Again")

    assert "error" in result
    assert result["budget"] == {"used": 2, "max": 2}
    # The third call never reached the underlying handler -- refused before dispatch.
    assert len(recorder) == 2


def test_wrap_play_interactive_close_never_costs_a_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ending a session early should never cost what it's trying to save."""
    monkeypatch.setattr("app.core.config.PLAY_INTERACTIVE_MAX_STEPS", 5)
    recorder: list = []
    ctx: dict = {}
    wrapped = wt._wrap_play_interactive(_make_handler(recorder), ctx)

    wrapped(action="open", url="https://example.com")
    result = wrapped(action="close")

    assert result["budget"]["used"] == 1  # only the open counted
    assert len(recorder) == 2  # close still reached the handler


def test_wrap_play_interactive_close_still_works_after_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close must always be allowed through, even with zero budget left, so a spent session can still be cleaned up."""
    monkeypatch.setattr("app.core.config.PLAY_INTERACTIVE_MAX_STEPS", 1)
    recorder: list = []
    ctx: dict = {}
    wrapped = wt._wrap_play_interactive(_make_handler(recorder), ctx)

    wrapped(action="open", url="https://example.com")
    result = wrapped(action="close")

    assert result["action"] == "close"
    assert "error" not in result


def test_wrap_play_interactive_does_not_charge_a_step_for_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong click_text guess shouldn't burn the exploration budget the model is trying to use well."""
    monkeypatch.setattr("app.core.config.PLAY_INTERACTIVE_MAX_STEPS", 5)

    def failing_handler(**_kwargs: object) -> dict:
        return {"error": "no element matching 'Nonexistent'"}

    ctx: dict = {}
    wrapped = wt._wrap_play_interactive(failing_handler, ctx)

    result = wrapped(action="click", target="Nonexistent")

    assert result["budget"]["used"] == 0


def test_wrap_play_interactive_shares_budget_across_calls_via_context() -> None:
    """The step counter lives in the shared compose context, not per-wrapper-instance state -- two wrap calls with the SAME context share one budget.

    ctx starts with a real key (matching every actual caller -- the
    compose context built in llm_compose.py always carries
    service_id/source_url/model already) rather than a bare {}: an empty
    dict is falsy in Python, so `context or {}` (the same pattern every
    wrapper in this module uses) would silently swap in a fresh disconnected
    dict instead of reusing it, defeating the very state-sharing this test
    means to check.
    """
    recorder: list = []
    ctx: dict = {"service_id": "test"}
    wrapped_a = wt._wrap_play_interactive(_make_handler(recorder), ctx)
    wrapped_b = wt._wrap_play_interactive(_make_handler(recorder), ctx)

    wrapped_a(action="open", url="https://example.com")
    result = wrapped_b(action="click", target="Start")

    assert result["budget"]["used"] == 2
