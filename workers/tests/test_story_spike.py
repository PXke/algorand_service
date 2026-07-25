"""abort_article tool (2026-07-17): lets the writer refuse to compose a story at all when research shows there is nothing real to report — the AlgoGlyph incident happened because the only available move, given clear dormancy signals, was "write it anyway"."""

from __future__ import annotations

import pytest

from app.modules.ai.story_spike import (
    ABORT_ARTICLE_SCHEMA,
    SPIKE_CATEGORIES,
    StorySpikedError,
    abort_article_handler,
)


def test_abort_article_handler_raises_with_category_and_reason() -> None:
    """Raises StorySpikedError carrying the given category and reason."""
    with pytest.raises(StorySpikedError) as exc_info:
        abort_article_handler(category="dead_project", reason="minted 2021, 12 holders, dormant")
    err = exc_info.value
    assert err.category == "dead_project"
    assert "12 holders" in err.reason


def test_unknown_category_falls_back_to_not_newsworthy() -> None:
    """Falls back to the "not_newsworthy" category when an unrecognized category is passed."""
    with pytest.raises(StorySpikedError) as exc_info:
        abort_article_handler(category="bogus", reason="whatever")
    assert exc_info.value.category == "not_newsworthy"


def test_reason_is_length_capped() -> None:
    """Caps an overlong spike reason to 500 characters."""
    with pytest.raises(StorySpikedError) as exc_info:
        abort_article_handler(category="dead_project", reason="x" * 5000)
    assert len(exc_info.value.reason) <= 500


def test_schema_declares_all_categories() -> None:
    """Declares every SPIKE_CATEGORIES value in the tool schema's category enum."""
    props = ABORT_ARTICLE_SCHEMA["function"]["parameters"]["properties"]
    assert set(props["category"]["enum"]) == set(SPIKE_CATEGORIES)
    assert "reason" in props


def test_registered_in_writer_tool_registry() -> None:
    """Registers abort_article among the writer's available tool schemas and handlers."""
    from app.modules.ai.writer_tools import all_tools

    schemas, handlers = all_tools(context={"service_id": "x", "source_url": "x", "model": "m"})
    names = {(s.get("function") or {}).get("name") for s in schemas}
    assert "abort_article" in names
    assert "abort_article" in handlers


def test_chat_with_tools_reraises_spike_and_records_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool loop's normal contract is 'a tool failure must not abort the article' — abort_article is the deliberate exception. It must escape the loop (not get swallowed into a {"error": ...} result) and the trace must still show the writer's stated reason for admin visibility."""
    from app.modules.ai.mistral_client import MistralClient

    client = MistralClient.__new__(MistralClient)
    client._api_key = "test-key"
    client._model = "test-model"
    client._metadata = {}
    client._reasoning_effort_unsupported = True
    trace: list = []

    def fake_post(_self: MistralClient, _payload: dict) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "abort_article",
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
    monkeypatch.setattr(MistralClient, "_log_task_context", lambda _self, *_a, **_k: None)

    with pytest.raises(StorySpikedError):
        client.chat_with_tools(
            [{"role": "user", "content": "research this"}],
            tools=[ABORT_ARTICLE_SCHEMA],
            handlers={"abort_article": abort_article_handler},
            trace=trace,
        )
    assert trace, "spike call must be recorded in the trace before re-raising"
    spike_entry = trace[-1]
    assert spike_entry["tool"] == "abort_article"
    assert spike_entry["result"]["spiked"] is True
    assert spike_entry["result"]["category"] == "dead_project"


# --- a self-negating spike must NOT abort (Pera Wallet recompose, 2026-07-20) ---
def test_no_spike_needed_reason_does_not_abort() -> None:
    """The writer misused abort_article to narrate 'No spike needed' and threw away a correct article. A reason that negates the spike returns a nudge instead."""
    out = abort_article_handler(
        category="insufficient_sources",
        reason="No spike needed. I have verified six distinct wallet ecosystems.",
    )
    assert out["aborted"] is False
    assert "write the full article" in out["note"].lower()


@pytest.mark.parametrize(
    "reason",
    [
        "No spike needed here.",
        "I will not spike this — the project is clearly active.",
        "Do not spike; enough sources verified.",
    ],
)
def test_various_negations_do_not_abort(reason: str) -> None:
    """Treats several phrasings of a self-negating "don't spike" reason as a non-abort."""
    out = abort_article_handler(category="not_newsworthy", reason=reason)
    assert out["aborted"] is False


def test_genuine_spike_still_aborts() -> None:
    """Still aborts for a genuine dead-project reason that contains no negation phrasing."""
    # a real dead-project reason (no negation) must still spike
    with pytest.raises(StorySpikedError):
        abort_article_handler(
            category="dead_project",
            reason="asset minted 2021, 12 holders, last transfer 2024, template site",
        )
