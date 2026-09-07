"""Per-LLM-call wall-clock timing in the compose transcript (2026-09-05, owner ask).

Each assistant turn the tool loop appends carries started_at_ms/ended_at_ms
measured at the actual provider request/response boundary, and the stage-2
write's synthetic turn carries the same — so a stored compose session records
when each call ran instead of leaving the timeline to be inferred from log
gaps. The annotation keys are local bookkeeping only: every outgoing
OpenAI-compatible request strips them (a stricter provider can reject unknown
message fields — the same replay-failure class as the tool_call_id backfill).
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.modules.ai.llm_compose import _append_stage2_debug_turn, _note_llm_call
from app.modules.ai.llm_openai_compatible import (
    _OpenAIToolLoopAdapter,
    _strip_local_annotations,
)
from app.modules.ai.llm_tool_loop import RoundResult


def test_strip_local_annotations_removes_timing_keys() -> None:
    """Timing keys are removed from outgoing copies; the original dicts are untouched."""
    annotated = {
        "role": "assistant",
        "content": "hi",
        "started_at_ms": 1,
        "ended_at_ms": 2,
    }
    clean = {"role": "user", "content": "q"}
    out = _strip_local_annotations([clean, annotated])
    assert out[1] == {"role": "assistant", "content": "hi"}
    assert annotated["started_at_ms"] == 1  # source not mutated
    # untouched messages pass through by reference (no copy cost)
    assert out[0] is clean


class _StubProvider:
    """The two attributes _OpenAIToolLoopAdapter.__init__ actually reads."""

    _metadata: ClassVar[dict[str, Any]] = {}

    def _effective_max_tokens(self, max_tokens: int | None) -> int:
        return max_tokens or 1000


def test_adapter_stamps_round_timing_on_assistant_turn() -> None:
    """append_assistant_turn carries the RoundResult's request/response window onto the conversation-history dict, without mutating the raw API message."""
    adapter = _OpenAIToolLoopAdapter(
        _StubProvider(),  # type: ignore[arg-type]
        [{"role": "user", "content": "q"}],
        handlers={},
        context_tokens=10_000,
        max_tokens=None,
    )
    adapter._convo = [{"role": "user", "content": "q"}]
    raw_msg = {"role": "assistant", "content": "answer"}
    adapter.append_assistant_turn(
        RoundResult(text="answer", raw=raw_msg, started_at_ms=100, ended_at_ms=250)
    )
    appended = adapter._convo[-1]
    assert appended["started_at_ms"] == 100
    assert appended["ended_at_ms"] == 250
    assert "started_at_ms" not in raw_msg  # raw response dict left untouched


def test_adapter_omits_timing_when_round_has_none() -> None:
    """An adapter/round that didn't measure (started/ended None) appends a clean turn with no annotation keys at all."""
    adapter = _OpenAIToolLoopAdapter(
        _StubProvider(),  # type: ignore[arg-type]
        [],
        handlers={},
        context_tokens=10_000,
        max_tokens=None,
    )
    adapter._convo = []
    adapter.append_assistant_turn(RoundResult(text="a", raw={"role": "assistant", "content": "a"}))
    assert "started_at_ms" not in adapter._convo[-1]
    assert "ended_at_ms" not in adapter._convo[-1]


def test_stage2_debug_turn_carries_write_call_timing() -> None:
    """The stage-2 write's synthetic assistant turn records the chat_json_object call's wall-clock window."""
    debug: dict[str, Any] = {"messages": []}
    _append_stage2_debug_turn(
        debug,
        "digest text",
        {"title": "t", "body": "b"},
        started_at_ms=1_757_000_000_000,
        ended_at_ms=1_757_000_090_000,
    )
    assistant = debug["messages"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["started_at_ms"] == 1_757_000_000_000
    assert assistant["ended_at_ms"] == 1_757_000_090_000


def test_note_llm_call_accumulates_and_tolerates_no_debug() -> None:
    """_note_llm_call appends {purpose, started, ended} entries to debug["llm_calls"], and is a no-op with debug=None (legacy/test callers)."""
    debug: dict[str, Any] = {}
    _note_llm_call(debug, "digest_synthesis", 10, 20)
    _note_llm_call(debug, "quality_rubric", 30, 45)
    assert debug["llm_calls"] == [
        {"purpose": "digest_synthesis", "started_at_ms": 10, "ended_at_ms": 20},
        {"purpose": "quality_rubric", "started_at_ms": 30, "ended_at_ms": 45},
    ]
    _note_llm_call(None, "digest_synthesis", 1, 2)  # must not raise
