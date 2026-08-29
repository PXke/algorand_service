"""DeepSeek's vision-capable writer/research model actually seeing a screenshot tool result, not just knowing a URL string exists.

deepseek-v4-flash-vision-exp (2026-08-21) can see an image, but only if it's
embedded as a real multimodal content block somewhere in the conversation.
DeepSeek's own docs are explicit that image content is accepted in
`user`-role messages only (system/assistant 400 outright), so a
`capture_screenshot` tool result (shaped ``{"url": ..., "image_url": ...,
"full_page": ...}``) can't just get its `content` swapped to a multimodal
list on the existing tool-role message -- see `_run_tool_call`/
`_vision_followup_message` in llm_openai_compatible.py. This file covers:
the follow-up image message appears only for a vision-capable model, only
when a tool result actually carries an `image_url`, and every other
combination is completely unaffected (the existing tool-role message is
untouched either way).
"""

from __future__ import annotations

import json
import unittest.mock

from app.core.config import (
    DEEPSEEK_MODEL_DIGEST,
    DEEPSEEK_MODEL_RESEARCH,
    DEEPSEEK_MODEL_RUBRIC,
    DEEPSEEK_MODEL_TRANSLATE,
    DEEPSEEK_MODEL_WRITER,
)
from app.modules.ai.llm_openai_compatible import (
    DeepSeekProvider,
    MistralProvider,
    OpenAICompatibleProvider,
)

_SCREENSHOT_RESULT = {
    "url": "https://example.com/game",
    "image_url": "https://cdn.example.com/screenshots/abc123.png",
    "full_page": False,
}


def _msg(content: str = "", tool_calls: list[dict] | None = None) -> dict:
    m: dict = {"content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return {"choices": [{"message": m}]}


def _screenshot_call(call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "function": {
            "name": "capture_screenshot",
            "arguments": json.dumps({"url": "https://example.com/game"}),
        },
    }


def _fetch_call(call_id: str = "call_2") -> dict:
    return {
        "id": call_id,
        "function": {"name": "fetch_url", "arguments": json.dumps({"url": "https://example.com"})},
    }


def _run_one_round(
    client: OpenAICompatibleProvider, *, tool_calls: list[dict], handlers: dict
) -> list[dict]:
    """Drive chat_with_tools for exactly one tool-call round then a final answer, returning the full constructed conversation (debug['messages'])."""
    seq = [_msg(tool_calls=tool_calls), _msg(content="FINAL")]
    calls = {"n": 0}

    def fake_post(_payload: dict) -> dict:
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    with unittest.mock.patch.object(client, "_post", side_effect=fake_post):
        debug: dict = {}
        out = client.chat_with_tools(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            tools=[],
            handlers=handlers,
            debug=debug,
        )
    assert out == "FINAL"
    return debug["messages"]


# --------------------------------------------------------------------------- #
# Config defaults
# --------------------------------------------------------------------------- #


def test_writer_and_research_default_off_the_experimental_vision_model() -> None:
    """Reverted 2026-08-28 (owner decision, Lumi Rogue): the vision-capable variant is an experimental release, and a real test of it produced an unused capture_screenshot result plus separate tool-discipline problems in the same compose. Both roles still get full tool access to capture_screenshot (research directly, writer via the revision pass and the legacy single-loop path) -- see llm_compose._compose_via_writer_tools_locked / _run_two_stage_compose -- capture_screenshot just returns an image_url without also feeding a vision follow-up on this model."""
    assert DEEPSEEK_MODEL_WRITER == "deepseek-v4-flash"
    assert DEEPSEEK_MODEL_RESEARCH == "deepseek-v4-flash"


def test_digest_still_defaults_to_the_vision_model_translate_and_rubric_do_not() -> None:
    """2026-08-27 put all five DeepSeek call sites on one shared model string; 2026-08-28 pulled writer/research/translate/rubric back off it (owner decision -- distrust of the experimental variant, see config.py's DEEPSEEK_MODEL_WRITER comment), leaving digest as the only one still on vision-exp. `_supports_vision()` reading True for digest is harmless either way: it only gates OPTIONAL image embedding when a tool result actually carries an image_url, which digest never produces regardless of what the model can see."""
    assert DEEPSEEK_MODEL_DIGEST == "deepseek-v4-flash-vision-exp"
    assert DEEPSEEK_MODEL_TRANSLATE == "deepseek-v4-flash"
    assert DEEPSEEK_MODEL_RUBRIC == "deepseek-v4-flash"


# --------------------------------------------------------------------------- #
# _supports_vision
# --------------------------------------------------------------------------- #


def test_deepseek_vision_model_supports_vision() -> None:
    """The vision-exp model string turns on _supports_vision."""
    assert DeepSeekProvider(api_key="k", model="deepseek-v4-flash-vision-exp")._supports_vision()


def test_deepseek_plain_chat_model_does_not_support_vision() -> None:
    """The plain text model (digest/translate/rubric) stays False -- per-model, not per-provider."""
    assert not DeepSeekProvider(api_key="k", model="deepseek-chat")._supports_vision()


def test_mistral_never_supports_vision() -> None:
    """Mistral has no vision-capable model wired here at all -- the base class default (False) must hold regardless of model string."""
    assert not MistralProvider(api_key="k")._supports_vision()


# --------------------------------------------------------------------------- #
# The actual embedding behavior
# --------------------------------------------------------------------------- #


def test_vision_model_gets_an_image_followup_after_a_screenshot_tool_result() -> None:
    """A capture_screenshot result on the vision model produces: the normal tool-role message (content unchanged, still the plain JSON string) immediately followed by a NEW user-role message carrying a multimodal content list with an image_url block pointing at the same URL."""
    client = DeepSeekProvider(api_key="k", model="deepseek-v4-flash-vision-exp")
    convo = _run_one_round(
        client,
        tool_calls=[_screenshot_call("call_1")],
        handlers={"capture_screenshot": lambda **_k: dict(_SCREENSHOT_RESULT)},
    )
    # Find the tool-role message.
    tool_idx = next(i for i, m in enumerate(convo) if m.get("role") == "tool")
    tool_msg = convo[tool_idx]
    assert tool_msg["tool_call_id"] == "call_1"
    # Tool-role content is completely unchanged: a plain JSON string.
    assert isinstance(tool_msg["content"], str)
    parsed = json.loads(tool_msg["content"])
    assert parsed["image_url"] == _SCREENSHOT_RESULT["image_url"]

    # The very next message is the vision followup: user-role, multimodal content.
    followup = convo[tool_idx + 1]
    assert followup["role"] == "user"
    assert isinstance(followup["content"], list)
    types = [b["type"] for b in followup["content"]]
    assert "text" in types
    assert "image_url" in types
    image_block = next(b for b in followup["content"] if b["type"] == "image_url")
    assert image_block["image_url"]["url"] == _SCREENSHOT_RESULT["image_url"]
    # The text block references the tool call so a transcript reader knows
    # what this image is.
    text_block = next(b for b in followup["content"] if b["type"] == "text")
    assert "capture_screenshot" in text_block["text"]
    assert "call_1" in text_block["text"]


def test_non_vision_deepseek_model_gets_no_image_followup() -> None:
    """Regression: the plain deepseek-chat model (digest/translate/rubric, or writer/research before this change) must see EXACTLY what it saw before -- the tool result's JSON string and nothing else. No multimodal message is ever constructed for it."""
    client = DeepSeekProvider(api_key="k", model="deepseek-chat")
    convo = _run_one_round(
        client,
        tool_calls=[_screenshot_call("call_1")],
        handlers={"capture_screenshot": lambda **_k: dict(_SCREENSHOT_RESULT)},
    )
    assert not any(isinstance(m.get("content"), list) for m in convo)
    tool_msgs = [m for m in convo if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert json.loads(tool_msgs[0]["content"])["image_url"] == _SCREENSHOT_RESULT["image_url"]


def test_mistral_gets_no_image_followup_even_with_a_screenshot_result() -> None:
    """Mistral never supports vision -- the exact same capture_screenshot result must be handled identically to today: URL string only, no multimodal block."""
    client = MistralProvider(api_key="k")
    convo = _run_one_round(
        client,
        tool_calls=[_screenshot_call("call_1")],
        handlers={"capture_screenshot": lambda **_k: dict(_SCREENSHOT_RESULT)},
    )
    assert not any(isinstance(m.get("content"), list) for m in convo)


def test_vision_model_unaffected_by_ordinary_tool_results() -> None:
    """The vast majority of tool calls (fetch_url, get_algo_market, etc.) never carry an image_url -- even on the vision model, these must produce no image followup and no change at all to the existing tool-role message shape."""
    client = DeepSeekProvider(api_key="k", model="deepseek-v4-flash-vision-exp")
    convo = _run_one_round(
        client,
        tool_calls=[_fetch_call("call_2")],
        handlers={"fetch_url": lambda **_k: {"url": "https://example.com", "text": "hello"}},
    )
    assert not any(isinstance(m.get("content"), list) for m in convo)
    tool_msgs = [m for m in convo if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert json.loads(tool_msgs[0]["content"]) == {"url": "https://example.com", "text": "hello"}


def test_error_result_never_produces_an_image_followup() -> None:
    """A tool call that raises (or an unknown tool name) produces an {"error": ...} result -- never image_url -- so it must never spawn a vision followup even on the vision model."""
    client = DeepSeekProvider(api_key="k", model="deepseek-v4-flash-vision-exp")

    def boom(**_kwargs: object) -> dict:
        raise RuntimeError("no browser session")

    convo = _run_one_round(
        client,
        tool_calls=[_screenshot_call("call_1")],
        handlers={"capture_screenshot": boom},
    )
    assert not any(isinstance(m.get("content"), list) for m in convo)
    tool_msgs = [m for m in convo if m.get("role") == "tool"]
    assert "error" in json.loads(tool_msgs[0]["content"])


def test_multiple_tool_calls_in_one_round_keep_tool_messages_contiguous() -> None:
    """A round with TWO tool calls (one screenshot, one plain fetch) must still produce every tool-role message immediately, contiguously, one per call_id, right after the assistant turn -- the API's own tool-calling protocol requires this, and _merged_convo_with_prior_debug's id-backfill re-pairing walks the transcript assuming exactly this shape (assistant, tool, tool, ...). The vision followup for the screenshot call must land AFTER both tool messages, never between them."""
    client = DeepSeekProvider(api_key="k", model="deepseek-v4-flash-vision-exp")
    convo = _run_one_round(
        client,
        tool_calls=[_screenshot_call("call_1"), _fetch_call("call_2")],
        handlers={
            "capture_screenshot": lambda **_k: dict(_SCREENSHOT_RESULT),
            "fetch_url": lambda **_k: {"url": "https://example.com", "text": "hello"},
        },
    )
    # The fake API response fixture (_msg) doesn't set an explicit "role" key
    # (real responses do; irrelevant to what's under test here) -- locate the
    # assistant turn by its tool_calls instead.
    assistant_idx = next(i for i, m in enumerate(convo) if m.get("tool_calls"))
    tool_msg_1 = convo[assistant_idx + 1]
    tool_msg_2 = convo[assistant_idx + 2]
    assert tool_msg_1["role"] == "tool"
    assert tool_msg_1["tool_call_id"] == "call_1"
    assert tool_msg_2["role"] == "tool"
    assert tool_msg_2["tool_call_id"] == "call_2"
    # The vision followup comes right after BOTH tool messages, not between them.
    followup = convo[assistant_idx + 3]
    assert followup["role"] == "user"
    assert isinstance(followup["content"], list)


def test_deepseek_provider_defaults_off_the_vision_model() -> None:
    """DeepSeekProvider() with no explicit model resolves to DEEPSEEK_MODEL_WRITER, reverted 2026-08-28 off the experimental vision-capable variant (see config.py's own comment) -- the vision mechanism itself still works, see test_deepseek_vision_model_supports_vision above, it's just not the default any more."""
    provider = DeepSeekProvider()
    assert provider.model == "deepseek-v4-flash"
    assert not provider._supports_vision()
