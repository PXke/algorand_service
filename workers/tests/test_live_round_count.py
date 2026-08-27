"""debug["rounds"] must update live, round by round, via on_round.

Not just once when chat_with_tools' whole multi-round loop finally returns.

Root-caused 2026-08-27 (live admin observation): every exit path in the loop only wrote
debug["rounds"] at loop TERMINATION (no more tool calls, out of rounds, or a salvaged
final article). A live checkpoint fired mid-loop by on_round (the exact mechanism
chat_with_tools' own docstring says exists "since trace/debug are mutated in place round
by round but nothing previously re-persisted them until the whole multi-round call
returned") always persisted rounds=0 regardless of how many rounds had genuinely
completed -- misleading on a long research pass, unlike the sibling tool_calls count
(derived from len(trace)) which already updated correctly every round.
"""

from __future__ import annotations

import pytest

from app.modules.ai.llm_openai_compatible import MistralProvider


def _msg(content: str = "", tool_calls: list[dict] | None = None) -> dict:
    m = {"content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def _tool_call(call_id: str) -> list[dict]:
    return [{"id": call_id, "function": {"name": "search_web", "arguments": "{}"}}]


def test_debug_rounds_updates_on_every_on_round_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 3-round tool-calling session reports the live round count at each on_round firing, not 0 until the end."""
    client = MistralProvider(api_key="test-key")
    seq = [
        _msg(tool_calls=_tool_call("1")),
        _msg(tool_calls=_tool_call("2")),
        _msg(content="FINAL"),
    ]
    calls = {"n": 0}

    def fake_post(payload: dict) -> dict:  # noqa: ARG001 -- name must match the real callee's keyword arg
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(client, "_post", fake_post)

    debug: dict = {}
    observed_rounds_at_checkpoint: list[int] = []

    def on_round() -> None:
        observed_rounds_at_checkpoint.append(debug.get("rounds", 0))

    client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"search_web": lambda **_k: {"ok": True}},
        debug=debug,
        on_round=on_round,
    )

    # Two rounds made tool calls (each fires on_round once, AFTER that round's
    # debug["rounds"] update); the third round has no tool calls and returns
    # directly without an on_round firing (matching the existing no-tool-calls
    # exit path) -- but debug["rounds"] must still reflect it happened.
    assert observed_rounds_at_checkpoint == [1, 2]
    assert debug["rounds"] == 3


def test_debug_rounds_still_correct_at_the_end_with_no_on_round_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting on_round entirely (most callers) must not regress the existing terminal debug["rounds"] value."""
    client = MistralProvider(api_key="test-key")
    seq = [_msg(tool_calls=_tool_call("1")), _msg(content="FINAL")]
    calls = {"n": 0}

    def fake_post(payload: dict) -> dict:  # noqa: ARG001 -- name must match the real callee's keyword arg
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(client, "_post", fake_post)

    debug: dict = {}
    client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"search_web": lambda **_k: {"ok": True}},
        debug=debug,
    )

    assert debug["rounds"] == 2
