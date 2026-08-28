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


def test_malformed_tool_arguments_are_rejected_without_running_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool call whose `arguments` string fails to json.loads() must not be silently coerced to {} and run anyway -- that would execute the handler with args the model never actually sent. It must be told its call was malformed instead."""
    client = MistralProvider(api_key="test-key")
    executed = {"n": 0}

    def handler(**_kwargs: object) -> dict:
        executed["n"] += 1
        return {"ok": True}

    seq = [
        _msg(
            tool_calls=[
                {"id": "1", "function": {"name": "fetch_url", "arguments": "{not valid json"}}
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
    trace: list = []
    out = client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"fetch_url": handler},
        trace=trace,
    )
    assert out == "DONE"
    assert executed["n"] == 0  # handler never ran against unparsed args
    assert trace[-1]["tool"] == "fetch_url"
    assert trace[-1]["result"] == {"error": "malformed tool arguments"}


def test_malformed_required_tool_call_does_not_satisfy_require_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed call to the mandatory tool never ran the handler, so it must not count as satisfying require_tool -- the model still gets nudged to call it for real."""
    client = MistralProvider(api_key="test-key")
    executed = {"n": 0}

    def handler(**_kwargs: object) -> dict:
        executed["n"] += 1
        return {"grade": 8}

    seq = [
        _msg(
            tool_calls=[{"id": "1", "function": {"name": "review_draft", "arguments": "{bad json"}}]
        ),
        _msg(content="STILL NOT DONE"),  # nudged (require_tool never satisfied)
        _msg(content="FINAL"),  # nudge-once exhausted, accepted anyway
    ]
    calls = {"n": 0}

    def fake_post(_payload: dict) -> dict:
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(client, "_post", fake_post)
    out = client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"review_draft": handler},
        require_tool="review_draft",
    )
    assert out == "FINAL"
    assert executed["n"] == 0  # the malformed call never actually ran review_draft
    assert calls["n"] == 3  # initial malformed call + one nudge + final accept


def test_capped_tool_refusal_does_not_satisfy_require_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call to the mandatory tool that gets refused outright by the per-session call cap never reached the handler either, so it must not satisfy require_tool -- same fix, same reasoning, as the malformed-arguments case above."""
    client = MistralProvider(api_key="test-key")
    executed = {"n": 0}

    def handler(**_kwargs: object) -> dict:
        executed["n"] += 1
        return {"listed": False}

    seq = [
        _msg(
            tool_calls=[
                {"id": "1", "function": {"name": "search_x", "arguments": '{"query":"new"}'}}
            ]
        ),
        _msg(content="STILL NOT DONE"),
        _msg(content="FINAL"),
    ]
    calls = {"n": 0}

    def fake_post(_payload: dict) -> dict:
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(client, "_post", fake_post)
    # Seed the per-tool call count at the cap (3) via the trace, as a real
    # cross-pass session would after 3 earlier search_x calls.
    trace = [
        {"tool": "search_x", "arguments": {"query": f"q{i}"}, "result": {"count": 0}}
        for i in range(3)
    ]
    out = client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"search_x": handler},
        require_tool="search_x",
        trace=trace,
    )
    assert out == "FINAL"
    assert executed["n"] == 0  # the capped call never actually ran search_x
    assert calls["n"] == 3  # initial capped call + one nudge + final accept


def test_exhaustion_finalizer_fits_budget_and_passes_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the loop runs out of rounds, the one extra completion asking for the final article must (a) trim the accumulated convo to the same budget the per-round loop enforces, and (b) honor the caller's own max_tokens instead of silently falling back to the provider default."""
    client = MistralProvider(api_key="test-key")

    def always_calls_a_tool(_payload: dict) -> dict:
        return _msg(tool_calls=[{"id": "1", "function": {"name": "fetch_url", "arguments": "{}"}}])

    monkeypatch.setattr(client, "_post", always_calls_a_tool)

    seen_budgets: list[int] = []
    seen_max_tokens: list[int | None] = []

    def fake_fit(_convo: list, budget: int) -> None:
        seen_budgets.append(budget)

    def fake_chat_completion(
        _messages: list[dict],
        *,
        json_object: bool = False,  # noqa: ARG001
        temperature: float = 0.6,  # noqa: ARG001
        max_tokens: int | None = None,
    ) -> str:
        seen_max_tokens.append(max_tokens)
        return "FINAL"

    monkeypatch.setattr("app.modules.ai.llm_openai_compatible.fit_messages_to_budget", fake_fit)
    monkeypatch.setattr(client, "chat_completion", fake_chat_completion)

    out = client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"fetch_url": lambda **_k: {"ok": True}},
        max_rounds=1,
        max_tokens=1234,
    )
    assert out == "FINAL"
    assert seen_max_tokens == [1234]
    assert seen_budgets  # fit_messages_to_budget ran again right before the finalizer
