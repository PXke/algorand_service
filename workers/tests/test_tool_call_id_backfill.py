"""A provider whose tool_calls come back with no `id` (DeepSeek, confirmed live 2026-08-13 against a real LumiRogue transcript) must not poison a later round's request. chat_with_tools backfills a synthetic id at the point a tool_calls list is first received, so it stays consistent between the echoed assistant message and the tool-result's tool_call_id -- and survives being echoed into a LATER call served by a stricter provider (Mistral's revision pass rejected the id-less history outright with "messages[N]: missing field `id`", silently dropping the revision-tool-call pass on every one of 5 real recompose attempts).

The same function also backfills a missing `type` -- confirmed live 2026-08-14: OpenAI's stricter API rejected a replayed tool_calls entry missing `type` ("Missing required parameter: 'messages[N].tool_calls[0].type'"), the identical failure shape on a different required field, hitting GPT-5.6-luna's revision pass on both of its first two real LumiRogue benchmark runs.
"""

import pytest

from app.modules.ai.mistral_client import MistralClient, _ensure_tool_call_ids


def _msg(content: str = "", tool_calls: list[dict] | None = None) -> dict:
    m = {"content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def _id_less_call(name: str = "fetch_url") -> dict:
    # Shaped exactly like the live DeepSeek response: no "id" key at all.
    return {"function": {"name": name, "arguments": "{}"}}


def test_ensure_tool_call_ids_backfills_a_missing_id() -> None:
    """A tool_call dict with no `id` key gets a non-empty synthetic one, mutated in place."""
    calls = [_id_less_call()]
    _ensure_tool_call_ids(calls)
    assert calls[0].get("id")


def test_ensure_tool_call_ids_leaves_a_real_id_untouched() -> None:
    """A provider that DOES send a real id (Mistral's own calls) is never overwritten."""
    calls = [{"id": "real-id-123", "function": {"name": "x", "arguments": "{}"}}]
    _ensure_tool_call_ids(calls)
    assert calls[0]["id"] == "real-id-123"


def test_ensure_tool_call_ids_backfills_a_missing_type() -> None:
    """A tool_call dict with no `type` key gets "function" backfilled, mutated in place -- same fix, same defensive point, as the `id` backfill above."""
    calls = [_id_less_call()]
    assert "type" not in calls[0]
    _ensure_tool_call_ids(calls)
    assert calls[0]["type"] == "function"


def test_ensure_tool_call_ids_leaves_a_real_type_untouched() -> None:
    """A provider that DOES send a real type is never overwritten."""
    calls = [{"id": "x", "type": "function", "function": {"name": "x", "arguments": "{}"}}]
    _ensure_tool_call_ids(calls)
    assert calls[0]["type"] == "function"


def test_id_less_tool_call_gets_a_consistent_id_in_echoed_history_and_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backfilled id must match between the assistant message's tool_calls[0].id (as later echoed back for round 2) and the paired tool-result message's tool_call_id -- otherwise the fix would just move the mismatch instead of fixing it."""
    client = MistralClient(api_key="test-key")
    payloads: list[dict] = []
    seq = [
        _msg(tool_calls=[_id_less_call()]),
        _msg(content="FINAL"),
    ]
    calls = {"n": 0}

    def fake_post(payload: dict) -> dict:
        payloads.append(payload)
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(client, "_post", fake_post)
    out = client.chat_with_tools(
        [{"role": "user", "content": "x"}],
        tools=[],
        handlers={"fetch_url": lambda **_k: {"ok": True}},
    )
    assert out == "FINAL"

    # Round 2's outgoing payload is what round 1's response looked like once
    # echoed back -- exactly the cross-round path that broke on a real
    # provider boundary.
    round2_messages = payloads[1]["messages"]
    assistant_msg = next(m for m in round2_messages if m.get("tool_calls"))
    tool_msg = next(m for m in round2_messages if m.get("role") == "tool")
    backfilled_id = assistant_msg["tool_calls"][0]["id"]
    assert backfilled_id
    assert tool_msg["tool_call_id"] == backfilled_id


def test_a_prior_debug_tool_message_missing_its_tool_call_id_gets_paired_on_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-caused 2026-08-15 (GPT-5.6-luna, live): a synthetic debug-transcript entry (mistral_compose's review_draft bookkeeping turn) can have an assistant tool_calls entry AND its paired tool-role message both missing their id/tool_call_id, with the two never matching. The id backfill on the assistant side alone isn't enough -- the merge point must also re-pair the following tool-role message's tool_call_id to the (possibly freshly-backfilled) assistant id, by position, the same 1:1 ordering _run_tool_call already produces for a real round."""
    client = MistralClient(api_key="test-key")
    payloads: list[dict] = []
    # Shaped exactly like the real bug: assistant tool_calls entry with no id,
    # paired tool-role message with no tool_call_id at all -- two separate gaps.
    debug = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "research this"},
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "review_draft", "arguments": "{}"}}],
            },
            {"role": "tool", "name": "review_draft", "content": "{}"},  # no tool_call_id key
        ]
    }

    def fake_post(payload: dict) -> dict:
        payloads.append(payload)
        return _msg(content="REVISED")

    monkeypatch.setattr(client, "_post", fake_post)
    client.chat_with_tools(
        [{"role": "system", "content": "revise sys"}, {"role": "user", "content": "revise this"}],
        tools=[],
        handlers={},
        debug=debug,
    )

    outgoing = payloads[0]["messages"]
    assistant_msg = next(m for m in outgoing if m.get("tool_calls"))
    tool_msg = next(
        m
        for m in outgoing
        if m.get("role") == "tool" and m.get("name") == "review_draft"
    )
    backfilled_id = assistant_msg["tool_calls"][0]["id"]
    assert backfilled_id
    assert tool_msg["tool_call_id"] == backfilled_id


def test_a_prior_debug_message_missing_its_id_gets_patched_on_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-caused 2026-08-13 (LumiRogue recompose ed06b874): the per-round backfill above guards messages THIS call generates, but a later stage (the revision pass) merges in `debug["messages"]` from an earlier stage via _merged_convo_with_prior_debug -- and something upstream of that merge could still leave an id-less tool_calls entry in there (the exact mechanism wasn't pinned down; static and synthetic testing of the per-round path alone couldn't reproduce a gap). Rather than leave the failure class open, the merge itself now re-asserts the id invariant on the WHOLE merged transcript, so a later call can never build a request with a naked tool_calls entry regardless of which earlier stage produced it."""
    client = MistralClient(api_key="test-key")
    payloads: list[dict] = []
    # Simulate a prior stage's transcript that somehow ended up with a
    # tool_calls entry missing an id -- exactly what Mistral's API rejects.
    debug = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "research this"},
            {"role": "assistant", "content": "", "tool_calls": [_id_less_call()]},
            {"role": "tool", "name": "fetch_url", "tool_call_id": "", "content": "{}"},
        ]
    }

    def fake_post(payload: dict) -> dict:
        payloads.append(payload)
        return _msg(content="REVISED")

    monkeypatch.setattr(client, "_post", fake_post)
    client.chat_with_tools(
        [{"role": "system", "content": "revise sys"}, {"role": "user", "content": "revise this"}],
        tools=[],
        handlers={},
        debug=debug,
    )

    outgoing = payloads[0]["messages"]
    assistant_msg = next(m for m in outgoing if m.get("tool_calls"))
    assert assistant_msg["tool_calls"][0].get("id")
    assert assistant_msg["tool_calls"][0].get("type") == "function"
