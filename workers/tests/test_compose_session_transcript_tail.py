"""record_compose_session must not silently drop the tail of a long compose transcript. Root-caused 2026-07-14/15 on a real NFT-marketplace article: the two-stage pipeline's most diagnostically important turns (the research->write digest handoff, review_draft/LLM-rubric grading, the final write) always come at the END of the transcript, after all research rounds — but a research-heavy story can exceed 60 messages on tool calls alone, and the old first-N slice silently dropped that entire tail. The grading itself still ran correctly (visible in final_output's heuristic_grade) — it was invisible in the admin Sessions transcript, which is what made this look like the LLM rubric never ran at all."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.modules.ai.tool_insights_store import record_compose_session


def test_long_transcript_keeps_review_draft_tail(fake_cassandra_session: MagicMock) -> None:
    """Keeps the review_draft tail of a 150+ message transcript instead of dropping it via a first-N slice."""
    # 150 research-round messages, then the review_draft turn that matters —
    # a first-N-of-60 slice would have dropped everything from index 60 on.
    messages = [
        {"role": "tool", "name": "search_web", "content": f"result {i}"} for i in range(150)
    ]
    messages.append(
        {
            "role": "assistant",
            "tool_calls": [{"function": {"name": "review_draft", "arguments": "{}"}}],
        }
    )
    messages.append({"role": "tool", "name": "review_draft", "content": '{"grade": 10.0}'})
    debug = {"messages": messages}

    record_compose_session(
        debug=debug,
        trace=[],
        service_id="svc",
        source_url="https://example.com/",
        model="mistral-large-latest",
        final_output="{}",
        status="ok",
    )

    call = fake_cassandra_session.execute.call_args
    stored_messages = json.loads(call.args[1][10])  # positional params, messages column
    names = [m.get("name") for m in stored_messages if m.get("name")]
    assert "review_draft" in names


def test_no_message_count_cap(fake_cassandra_session: MagicMock) -> None:
    """Stores all 200 messages of a transcript with no fixed message-count cap."""
    messages = [{"role": "tool", "name": "search_web", "content": "x"} for _ in range(200)]
    debug = {"messages": messages}

    record_compose_session(
        debug=debug,
        trace=[],
        service_id="svc",
        source_url="https://example.com/",
        model="mistral-large-latest",
        final_output="{}",
        status="ok",
    )

    call = fake_cassandra_session.execute.call_args
    stored_messages = json.loads(call.args[1][10])
    assert len(stored_messages) == 200


def test_oversized_transcript_drops_from_front_not_json_string(
    fake_cassandra_session: MagicMock,
) -> None:
    """A transcript so large its JSON exceeds the storage cap must still be stored as VALID json — dropping oldest (least valuable) entries whole, never a raw character slice through the middle of an object, which would corrupt the column for every reader (the Sessions page's json.loads)."""
    big_content = "x" * 2000
    messages = [{"role": "tool", "name": "search_web", "content": big_content} for _ in range(100)]
    messages.append({"role": "tool", "name": "review_draft", "content": '{"grade": 10.0}'})
    debug = {"messages": messages}

    record_compose_session(
        debug=debug,
        trace=[],
        service_id="svc",
        source_url="https://example.com/",
        model="mistral-large-latest",
        final_output="{}",
        status="ok",
    )

    call = fake_cassandra_session.execute.call_args
    raw = call.args[1][10]
    assert len(raw) <= 120_000
    stored_messages = json.loads(raw)  # must not raise — valid JSON, not a mid-string cut
    names = [m.get("name") for m in stored_messages if m.get("name")]
    assert "review_draft" in names  # the tail survives; front entries were dropped instead
