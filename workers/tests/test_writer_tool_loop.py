"""The agentic compose loop must not finish until the mandatory self-review tool (review_draft) has been called at least once."""

import pytest

from app.modules.ai.llm_openai_compatible import MistralProvider


def _msg(content: str = "", tool_calls: list[dict] | None = None) -> dict:
    m = {"content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def _review_call() -> list[dict]:
    return [{"id": "1", "function": {"name": "review_draft", "arguments": "{}"}}]


def test_require_tool_forces_review_before_finishing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The compose loop nudges the model to call review_draft before accepting its final output."""
    client = MistralProvider(api_key="test-key")
    seq = [
        _msg(content='{"title":"t","body":"b"}'),  # tries to finish, no review
        _msg(tool_calls=_review_call()),  # after nudge: calls review_draft
        _msg(content="FINAL"),  # finishes
    ]
    calls = {"n": 0}

    def fake_post(_payload: dict) -> dict:
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(client, "_post", fake_post)
    trace: list = []
    out = client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"review_draft": lambda **_k: {"grade": 8}},
        trace=trace,
        require_tool="review_draft",
    )
    assert out == "FINAL"
    assert any(t["tool"] == "review_draft" for t in trace)


def test_require_tool_nudges_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # If the model keeps refusing, we nudge once then accept its output (no
    # infinite loop).
    """A model that keeps refusing the required tool is nudged only once, then its output is accepted."""
    client = MistralProvider(api_key="test-key")
    calls = {"n": 0}

    def fake_post(_payload: dict) -> dict:
        calls["n"] += 1
        return _msg(content="STUBBORN")  # never calls the tool

    monkeypatch.setattr(client, "_post", fake_post)
    out = client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"review_draft": lambda **_k: {"grade": 1}},
        require_tool="review_draft",
    )
    assert out == "STUBBORN"
    assert calls["n"] == 2  # initial + one nudge, then accept


def test_debug_transcript_accumulates_across_multiple_chat_with_tools_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two-stage compose invokes chat_with_tools more than once against the SAME debug dict (initial research, then a RESEARCH_FLOOR nudge pass or a digest gap-fill pass). The second call must not silently overwrite the first round's tool calls out of the persisted/audited transcript — this was a real bug: `debug["messages"]` got reassigned to a fresh 2-message list every invocation, dropping earlier rounds even though `trace` (mutated by reference) kept accumulating correctly underneath it."""
    client = MistralProvider(api_key="test-key")

    def make_seq(marker: str) -> list[dict]:
        return [
            _msg(
                tool_calls=[
                    {
                        "id": "1",
                        "function": {"name": "fetch_url", "arguments": f'{{"url":"{marker}"}}'},
                    }
                ]
            ),
            _msg(content=f"done-{marker}"),
        ]

    round1 = make_seq("round1")
    round2 = make_seq("round2")
    calls = {"n": 0}
    combined = round1 + round2

    def fake_post(_payload: dict) -> dict:
        i = calls["n"]
        calls["n"] += 1
        return combined[i]

    monkeypatch.setattr(client, "_post", fake_post)
    trace: list = []
    debug: dict = {}

    client.chat_with_tools(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "round1 prompt"}],
        tools=[],
        handlers={"fetch_url": lambda **_k: {"ok": True}},
        trace=trace,
        debug=debug,
    )
    first_round_messages = list(debug["messages"])
    assert any("round1 prompt" in str(m.get("content", "")) for m in first_round_messages)

    client.chat_with_tools(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "round2 prompt"}],
        tools=[],
        handlers={"fetch_url": lambda **_k: {"ok": True}},
        trace=trace,
        debug=debug,
    )

    # Both rounds' prompts must survive in the persisted transcript.
    all_content = " ".join(str(m.get("content", "")) for m in debug["messages"])
    assert "round1 prompt" in all_content
    assert "round2 prompt" in all_content
    # trace already accumulated correctly by reference — the real bug was
    # only in the debug transcript, but assert it here too as a sanity check.
    assert len(trace) == 2


def test_cross_pass_dedup_seeds_seen_calls_from_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-07-16 audit finding: the research floor / gap-fill passes call chat_with_tools again with a fresh conversation but the SAME shared trace, and the per-call dedup cache started empty — so a later pass happily re-ran an earlier pass's identical searches (a real RandGallery session repeated 5 of its 35 tool calls). Seeding seen_calls from the trace makes an exact repeat in pass 2 a no-execute nudge, same as within one pass."""
    client = MistralProvider(api_key="test-key")
    executed = {"n": 0}

    def handler(**_kwargs: object) -> dict:
        executed["n"] += 1
        return {"data": "fresh"}

    seq = [
        _msg(
            tool_calls=[
                {
                    "id": "1",
                    "function": {
                        "name": "search_web",
                        "arguments": '{"query": "RandGallery closing"}',
                    },
                }
            ]
        ),
        _msg(content="DONE"),
    ]
    calls = {"n": 0}

    def fake_post(_payload: dict) -> dict:
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(client, "_post", fake_post)
    # Trace already contains this exact call from a previous pass.
    trace = [
        {
            "tool": "search_web",
            "arguments": {"query": "RandGallery closing"},
            "result": {"results": ["earlier data"]},
        }
    ]
    out = client.chat_with_tools(
        [{"role": "user", "content": "pass 2"}],
        tools=[],
        handlers={"search_web": handler},
        trace=trace,
    )
    assert out == "DONE"
    assert executed["n"] == 0  # the duplicate was nudged, never re-executed


def test_cross_pass_dedup_still_allows_retry_of_errored_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A transient failure in pass 1 must stay retryable in pass 2.
    """An earlier pass's errored tool call is still retried, not treated as an already-seen duplicate."""
    client = MistralProvider(api_key="test-key")
    executed = {"n": 0}

    def handler(**_kwargs: object) -> dict:
        executed["n"] += 1
        return {"data": "second attempt worked"}

    seq = [
        _msg(
            tool_calls=[
                {
                    "id": "1",
                    "function": {
                        "name": "fetch_url",
                        "arguments": '{"url": "https://example.com/"}',
                    },
                }
            ]
        ),
        _msg(content="DONE"),
    ]
    calls = {"n": 0}

    def fake_post(_payload: dict) -> dict:
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(client, "_post", fake_post)
    trace = [
        {
            "tool": "fetch_url",
            "arguments": {"url": "https://example.com/"},
            "result": {"error": "timeout"},
        }
    ]
    out = client.chat_with_tools(
        [{"role": "user", "content": "pass 2"}],
        tools=[],
        handlers={"fetch_url": handler},
        trace=trace,
    )
    assert out == "DONE"
    assert executed["n"] == 1  # errored call re-ran
