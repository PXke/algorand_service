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


def test_initial_source_material_turn_gets_a_generous_cap(
    fake_cassandra_session: MagicMock,
) -> None:
    """Messina.one regression pin (2026-08-02): the FIRST user turn carries the actual scraped source material -- the primary evidence for whether a specific claim was grounded or invented. At the old 1500-char generic cap it cut off after ~30 lines of a multi-page SERVICE WATCH aggregate, making a claim sourced from page 3 of the scrape look unsourced when it wasn't. Both known opening templates start with "Write the article now"."""
    source_material = "y" * 18_000
    messages = [
        {"role": "system", "content": "system prompt"},
        {
            "role": "user",
            "content": f"Write the article now from the material below.\n\n```\n{source_material}\n```",
        },
    ]
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
    user_turn = next(m for m in stored_messages if m["role"] == "user")
    assert source_material in user_turn["content"]  # not truncated away


def test_oversized_transcript_keeps_the_opening_system_user_pair(
    fake_cassandra_session: MagicMock,
) -> None:
    """Regression pin (2026-09-05): the front-drop eviction must start AFTER the opening system+user pair. The plain pop(0) used before evicted exactly those two messages first on any long session -- deleting the compose's actual prompt (the primary grounding evidence the Messina fix gives a 20k cap) and making stored transcripts open cold at an assistant turn."""
    source_material = "y" * 10_000
    messages: list[dict] = [
        {"role": "system", "content": "system prompt"},
        {
            "role": "user",
            "content": f"Write the article now from the material below.\n{source_material}",
        },
    ]
    messages += [{"role": "tool", "name": "search_web", "content": "x" * 2000} for _ in range(100)]
    messages.append({"role": "tool", "name": "review_draft", "content": '{"grade": 10.0}'})
    debug = {"messages": messages}

    record_compose_session(
        debug=debug,
        trace=[],
        service_id="svc",
        source_url="https://example.com/",
        model="deepseek-v4-flash",
        final_output="{}",
        status="ok",
    )

    call = fake_cassandra_session.execute.call_args
    raw = call.args[1][10]
    assert len(raw) <= 120_000
    stored_messages = json.loads(raw)
    assert stored_messages[0]["role"] == "system"
    assert stored_messages[1]["role"] == "user"
    assert source_material in stored_messages[1]["content"]  # the opening frame survives
    names = [m.get("name") for m in stored_messages if m.get("name")]
    assert "review_draft" in names  # the tail still survives too
    assert len(stored_messages) < 103  # middle research rounds were what got evicted


def test_per_call_timing_annotations_are_persisted(fake_cassandra_session: MagicMock) -> None:
    """started_at_ms/ended_at_ms stamped at the LLM request/response boundary (tool-loop rounds, the stage-2 write) must survive into the stored transcript -- the whole point is recording when each call actually ran instead of inferring the timeline from log gaps (2026-09-05, owner ask)."""
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "Write the article now from the material below."},
        {
            "role": "assistant",
            "content": "researching",
            "started_at_ms": 1_757_000_000_000,
            "ended_at_ms": 1_757_000_042_000,
        },
    ]
    debug = {"messages": messages}

    record_compose_session(
        debug=debug,
        trace=[],
        service_id="svc",
        source_url="https://example.com/",
        model="deepseek-v4-flash",
        final_output="{}",
        status="ok",
    )

    call = fake_cassandra_session.execute.call_args
    stored_messages = json.loads(call.args[1][10])
    assistant = next(m for m in stored_messages if m["role"] == "assistant")
    assert assistant["started_at_ms"] == 1_757_000_000_000
    assert assistant["ended_at_ms"] == 1_757_000_042_000


def test_llm_call_log_rides_as_one_trailing_entry(fake_cassandra_session: MagicMock) -> None:
    """Ephemeral LLM calls with no message of their own (digest synthesis, rubric grading, ...) are persisted as ONE trailing llm_call_log entry built from debug["llm_calls"] -- appended only to the STORED array, never to debug["messages"], so it can never be replayed into a later revision-pass API request."""
    debug = {
        "messages": [{"role": "assistant", "content": "draft"}],
        "llm_calls": [
            {
                "purpose": "digest_synthesis",
                "started_at_ms": 1_757_000_100_000,
                "ended_at_ms": 1_757_000_160_000,
            }
        ],
    }

    record_compose_session(
        debug=debug,
        trace=[],
        service_id="svc",
        source_url="https://example.com/",
        model="deepseek-v4-flash",
        final_output="{}",
        status="ok",
    )

    call = fake_cassandra_session.execute.call_args
    stored_messages = json.loads(call.args[1][10])
    assert stored_messages[-1]["role"] == "llm_call_log"
    assert "digest_synthesis" in stored_messages[-1]["content"]
    # the live debug transcript itself was NOT polluted with a synthetic turn
    assert [m["role"] for m in debug["messages"]] == ["assistant"]


def test_other_user_turns_keep_the_short_cap(fake_cassandra_session: MagicMock) -> None:
    """A user turn that ISN'T the initial source-material prompt (e.g. a mid-loop nudge) still gets the tight 1500-char cap -- the generous cap is scoped to the one message shape that actually needs it."""
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "Some other user turn: " + ("z" * 3000)},
    ]
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
    user_turn = next(m for m in stored_messages if m["role"] == "user")
    assert len(user_turn["content"]) == 1500
