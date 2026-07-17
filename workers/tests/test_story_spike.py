"""spike_story tool (2026-07-17): lets the writer refuse to compose a story
at all when research shows there is nothing real to report — the AlgoGlyph
incident happened because the only available move, given clear dormancy
signals, was "write it anyway".
"""

from __future__ import annotations

import pytest

from app.modules.ai.story_spike import (
    SPIKE_CATEGORIES,
    SPIKE_STORY_SCHEMA,
    StorySpikedError,
    spike_story_handler,
)


def test_spike_story_handler_raises_with_category_and_reason() -> None:
    with pytest.raises(StorySpikedError) as exc_info:
        spike_story_handler(category="dead_project", reason="minted 2021, 12 holders, dormant")
    err = exc_info.value
    assert err.category == "dead_project"
    assert "12 holders" in err.reason


def test_unknown_category_falls_back_to_not_newsworthy() -> None:
    with pytest.raises(StorySpikedError) as exc_info:
        spike_story_handler(category="bogus", reason="whatever")
    assert exc_info.value.category == "not_newsworthy"


def test_reason_is_length_capped() -> None:
    with pytest.raises(StorySpikedError) as exc_info:
        spike_story_handler(category="dead_project", reason="x" * 5000)
    assert len(exc_info.value.reason) <= 500


def test_schema_declares_all_categories() -> None:
    props = SPIKE_STORY_SCHEMA["function"]["parameters"]["properties"]
    assert set(props["category"]["enum"]) == set(SPIKE_CATEGORIES)
    assert "reason" in props


def test_registered_in_writer_tool_registry() -> None:
    from app.modules.ai.writer_tools import all_tools

    schemas, handlers = all_tools(context={"service_id": "x", "source_url": "x", "model": "m"})
    names = {(s.get("function") or {}).get("name") for s in schemas}
    assert "spike_story" in names
    assert "spike_story" in handlers


def test_chat_with_tools_reraises_spike_and_records_trace(monkeypatch) -> None:
    """The tool loop's normal contract is 'a tool failure must not abort the
    article' — spike_story is the deliberate exception. It must escape the
    loop (not get swallowed into a {"error": ...} result) and the trace must
    still show the writer's stated reason for admin visibility."""
    from app.modules.ai.mistral_client import MistralClient

    client = MistralClient.__new__(MistralClient)
    client._api_key = "test-key"
    client._model = "test-model"
    client._metadata = {}
    client._reasoning_effort_unsupported = True
    trace: list = []

    def fake_post(self, payload):
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "spike_story",
                                    "arguments": (
                                        '{"category": "dead_project", '
                                        '"reason": "no on-chain activity since 2021"}'
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        }

    monkeypatch.setattr(MistralClient, "_post", fake_post)
    monkeypatch.setattr(MistralClient, "_log_task_context", lambda self, *a, **k: None)

    with pytest.raises(StorySpikedError):
        client.chat_with_tools(
            [{"role": "user", "content": "research this"}],
            tools=[SPIKE_STORY_SCHEMA],
            handlers={"spike_story": spike_story_handler},
            trace=trace,
        )
    assert trace, "spike call must be recorded in the trace before re-raising"
    spike_entry = trace[-1]
    assert spike_entry["tool"] == "spike_story"
    assert spike_entry["result"]["spiked"] is True
    assert spike_entry["result"]["category"] == "dead_project"
