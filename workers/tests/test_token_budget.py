"""Token-aware context management: structure-preserving tool-result capping and
oldest-first eliding when the conversation nears the model's context window."""

from __future__ import annotations

import json

from app.modules.ai.token_budget import (
    estimate_message_tokens,
    estimate_tokens,
    fit_messages_to_budget,
    serialize_tool_result,
)


def test_estimate_tokens_scales_with_length() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 320) > estimate_tokens("a" * 32)


def test_serialize_small_result_is_untouched() -> None:
    result = {"url": "https://x.io", "text": "short body", "links": [{"url": "https://y.io"}]}
    out = serialize_tool_result(result, max_chars=10_000)
    assert json.loads(out) == result  # full, valid, unchanged


def test_serialize_big_result_trims_text_but_keeps_links() -> None:
    result = {
        "url": "https://x.io",
        "title": "XBTO expands",
        "text": "A" * 50_000,
        "links": [{"text": "gov", "url": "https://algorand.foundation/gov"}],
    }
    out = serialize_tool_result(result, max_chars=4_000)
    assert len(out) <= 4_000
    parsed = json.loads(out)  # still VALID json (old code cut mid-string)
    # The bulky field was trimmed, but the high-signal fields survived.
    assert parsed["url"] == "https://x.io"
    assert parsed["title"] == "XBTO expands"
    assert parsed["links"] == [{"text": "gov", "url": "https://algorand.foundation/gov"}]
    assert parsed["_truncated"] is True
    assert len(parsed["text"]) < 50_000


def test_fit_elides_oldest_tool_results_first() -> None:
    convo = [
        {"role": "system", "content": "S" * 400},
        {"role": "user", "content": "U" * 400},
        {"role": "tool", "name": "fetch_url", "content": "OLD" + "x" * 4_000},
        {"role": "assistant", "content": "draft so far"},
        {"role": "tool", "name": "fetch_url", "content": "NEW" + "y" * 4_000},
    ]
    before = estimate_message_tokens(convo)
    after = fit_messages_to_budget(convo, budget_tokens=before - 500)
    assert after <= before - 500
    # Oldest tool result elided first; newest kept; non-tool roles untouched.
    assert "elided" in convo[2]["content"]
    assert convo[4]["content"].startswith("NEW")
    assert convo[0]["content"] == "S" * 400
    assert convo[3]["content"] == "draft so far"


def test_fit_noop_when_under_budget() -> None:
    convo = [{"role": "tool", "name": "t", "content": "small"}]
    snapshot = [dict(m) for m in convo]
    fit_messages_to_budget(convo, budget_tokens=10_000)
    assert convo == snapshot
