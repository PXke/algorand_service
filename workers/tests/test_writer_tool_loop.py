"""The agentic compose loop must not finish until the mandatory self-review
tool (review_draft) has been called at least once."""

from app.modules.ai.mistral_client import MistralClient


def _msg(content="", tool_calls=None):
    m = {"content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def _review_call():
    return [{"id": "1", "function": {"name": "review_draft", "arguments": "{}"}}]


def test_require_tool_forces_review_before_finishing(monkeypatch) -> None:
    client = MistralClient(api_key="test-key")
    seq = [
        _msg(content='{"title":"t","body":"b"}'),  # tries to finish, no review
        _msg(tool_calls=_review_call()),  # after nudge: calls review_draft
        _msg(content="FINAL"),  # finishes
    ]
    calls = {"n": 0}

    def fake_post(payload):
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(client, "_post", fake_post)
    trace: list = []
    out = client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"review_draft": lambda **k: {"grade": 8}},
        trace=trace,
        require_tool="review_draft",
    )
    assert out == "FINAL"
    assert any(t["tool"] == "review_draft" for t in trace)


def test_require_tool_nudges_only_once(monkeypatch) -> None:
    # If the model keeps refusing, we nudge once then accept its output (no
    # infinite loop).
    client = MistralClient(api_key="test-key")
    calls = {"n": 0}

    def fake_post(payload):
        calls["n"] += 1
        return _msg(content="STUBBORN")  # never calls the tool

    monkeypatch.setattr(client, "_post", fake_post)
    out = client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"review_draft": lambda **k: {"grade": 1}},
        require_tool="review_draft",
    )
    assert out == "STUBBORN"
    assert calls["n"] == 2  # initial + one nudge, then accept
