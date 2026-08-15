"""show_round_budget: a live "round N of M, K remain" note injected into each outgoing research-pass request, on top of the one-time static RESEARCH BUDGET mention in the system prompt. Must never be persisted into the transcript -- it needs to update every round, and the reasoning_content incident (2026-08-06) already proved that anything echoed back into `convo` compounds across rounds."""

from __future__ import annotations

import pytest

from app.modules.ai.mistral_client import MistralClient


def _msg(content: str = "", tool_calls: list[dict] | None = None) -> dict:
    m = {"content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def _tool_call() -> list[dict]:
    return [{"id": "1", "function": {"name": "search_web", "arguments": "{}"}}]


def test_round_budget_note_mid_budget() -> None:
    """A round with headroom left states how many rounds remain and frames depth as cheap."""
    note = MistralClient._round_budget_note(4, 24)
    assert "round 5 of 24" in note
    assert "19 remain" in note
    assert "cheap" in note


def test_round_budget_note_on_last_round() -> None:
    """The final round says so explicitly, telling the model to wrap up rather than start something new."""
    note = MistralClient._round_budget_note(23, 24)
    assert "LAST round" in note
    assert "Wrap up" in note


def test_default_off_no_note_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    """show_round_budget defaults to False -- existing callers (revision, single-stage) are unaffected."""
    client = MistralClient(api_key="test-key")
    payloads: list[dict] = []

    def fake_post(payload: dict) -> dict:
        payloads.append(payload)
        return _msg(content="FINAL")

    monkeypatch.setattr(client, "_post", fake_post)
    client.chat_with_tools([{"role": "user", "content": "x"}], tools=[], handlers={})
    assert not any(
        "research budget" in str(m.get("content", "")).lower() for m in payloads[0]["messages"]
    )


def test_enabled_note_appears_in_outgoing_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """With show_round_budget=True, each round's outgoing request carries a fresh note."""
    client = MistralClient(api_key="test-key")
    payloads: list[dict] = []
    seq = [_msg(tool_calls=_tool_call()), _msg(content="FINAL")]
    calls = {"n": 0}

    def fake_post(payload: dict) -> dict:
        payloads.append(payload)
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(client, "_post", fake_post)
    client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"search_web": lambda **_k: {"ok": True}},
        show_round_budget=True,
    )
    assert len(payloads) == 2
    round1_note = payloads[0]["messages"][-1]["content"]
    round2_note = payloads[1]["messages"][-1]["content"]
    assert "round 1 of" in round1_note
    assert "round 2 of" in round2_note


def test_note_never_persisted_into_the_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """The note must not leak into debug['messages'] -- it would otherwise compound across rounds exactly like the reasoning_content bug (2026-08-06)."""
    client = MistralClient(api_key="test-key")
    seq = [_msg(tool_calls=_tool_call()), _msg(content="FINAL")]
    calls = {"n": 0}

    def fake_post(_payload: dict) -> dict:
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(client, "_post", fake_post)
    debug: dict = {}
    client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"search_web": lambda **_k: {"ok": True}},
        show_round_budget=True,
        debug=debug,
    )
    persisted = debug.get("messages") or []
    assert not any("research budget" in str(m.get("content", "")).lower() for m in persisted)


def test_note_reflects_the_actual_max_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stated ceiling tracks whatever max_rounds this specific call was given, not a hardcoded number."""
    client = MistralClient(api_key="test-key")
    payloads: list[dict] = []

    def fake_post(payload: dict) -> dict:
        payloads.append(payload)
        return _msg(content="FINAL")

    monkeypatch.setattr(client, "_post", fake_post)
    client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={},
        show_round_budget=True,
        max_rounds=7,
    )
    assert "round 1 of 7" in payloads[0]["messages"][-1]["content"]
