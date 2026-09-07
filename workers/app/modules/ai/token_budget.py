"""Token-aware context management for the writer's agentic tool loop.

Cost is not the constraint (Mistral usage is effectively unlimited here) — the
context window is. So instead of a blind per-tool-result character cap (which cut
JSON mid-string, produced invalid payloads, and silently dropped trailing fields
like a page's `links` list), we keep tool results generous and only trim when the
WHOLE conversation approaches the model's context limit.

No tokenizer dependency is available in this environment, so token counts are
estimated from character length with a fixed chars-per-token ratio.

That ratio was chosen believing it would always OVER-count. Measured against
real prod usage numbers it does not, and the direction depends on the content:

  * prose / markdown (a Stage-2 write call, 2 messages, 215_667 content chars
    -> 62_463 real prompt_tokens) estimates ~8% HIGH, as intended;
  * a long tool-heavy merged transcript (224 messages, 372_840 content chars
    -> 134_507 real prompt_tokens) estimates ~9% LOW — dense tool-result JSON
    (escapes, punctuation, ids, URLs) really runs ~2.8 chars/token, not 3.2.

So on exactly the conversations this module exists to trim, the estimate is an
UNDER-count, and fit_messages_to_budget can return "fits" for a request that
does not. Verified 2026-09-05 in prod: a request trimmed to the 212_000-token
budget came back reporting 223_058 real prompt_tokens. Nothing failed, because
the response reserve (40_000) absorbed the overshoot well inside the provider's
real window — LLM_CONTEXT_SAFETY_TOKENS (4_000) on its own would not have.
Do not treat a pass through fit_messages_to_budget as proof a request fits.
"""

from __future__ import annotations

import json
from typing import Any

# Mistral's Tekken tokenizer averages ~3.5-4 chars/token on English + markdown,
# so this LOW ratio over-counts prose. It does NOT over-count dense tool-result
# JSON, which measures ~2.8 chars/token in prod — see the module docstring for
# the numbers. Raising the estimate to match (i.e. LOWERING this constant) would
# make every long research conversation trim earlier and elide more tool
# results, which is a compose-grounding change, not a pure safety fix: it is an
# owner decision, deliberately not taken here.
_CHARS_PER_TOKEN = 3.2
# Per-message envelope (role, delimiters, tool_call ids) the API counts on top of
# the content itself.
_MESSAGE_OVERHEAD_TOKENS = 4
# DeepSeek's vision-exp docs cap every embedded image at this many input
# tokens regardless of resolution -- used as a flat per-image estimate below
# (a multimodal `content` list's image_url blocks) since there's no way to
# derive the real count without the provider's own tokenizer. Deliberately
# the documented CEILING, in keeping with this module's over-count-not-under
# philosophy.
_IMAGE_TOKENS = 384

_ELIDED = json.dumps({"note": "[earlier tool result elided to fit the context window]"})

_SERIALIZER_TRUNCATION_BANNER = (
    "\n\n[... TEXT TRUNCATED BY SERIALIZER TO FIT CONTEXT BUDGET. "
    "{omitted:,} CHARACTERS OMITTED. USE TARGETED SEARCHES, fetch_url WITH "
    "continue_reading=true, OR SPECIFIC SUB-LINKS TO ACCESS OMITTED SECTIONS ...]"
)


def estimate_tokens(text: str) -> int:
    """Rough token count for a string (no tokenizer dep). See _CHARS_PER_TOKEN."""
    if not text:
        return 0
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Approximate total tokens of a chat conversation: content + serialized tool-call arguments + a small per-message envelope."""
    total = 0
    for m in messages:
        total += _MESSAGE_OVERHEAD_TOKENS
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # A multimodal content-blocks list (see llm_openai_compatible's
            # vision followup messages): text blocks count normally, an
            # image_url block is flat-rated at _IMAGE_TOKENS regardless of
            # size (no tokenizer available to do better -- see its own
            # comment).
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "image_url":
                    total += _IMAGE_TOKENS
                else:
                    total += estimate_tokens(str(block.get("text", "")))
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            total += estimate_tokens(str(fn.get("name", "")))
            total += estimate_tokens(str(fn.get("arguments", "")))
    return total


def serialize_tool_result(result: Any, max_chars: int) -> str:  # noqa: ANN401 -- arbitrary tool-result shape
    """JSON-serialize a tool result capped at ``max_chars`` WITHOUT mangling the structure: if it's too big, trim only the single longest string field (e.g. a page's `text`/`body`) and keep everything else, so `links`, `url`, and `title` still reach the model. Replaces the old ``json.dumps(result)[:N]`` that cut mid-string into invalid JSON and dropped trailing fields."""
    blob = json.dumps(result)
    if len(blob) <= max_chars:
        return blob
    if isinstance(result, dict):
        str_fields = [k for k, v in result.items() if isinstance(v, str) and v]
        if str_fields:
            biggest = max(str_fields, key=lambda k: len(result[k]))
            overflow = len(blob) - max_chars
            # Reserve space for the explicit truncation banner inside the field.
            banner_reserve = 220
            keep = max(0, len(result[biggest]) - overflow - banner_reserve)
            omitted = len(result[biggest]) - keep
            banner = _SERIALIZER_TRUNCATION_BANNER.format(omitted=max(0, omitted))
            trimmed = dict(result)
            trimmed[biggest] = result[biggest][:keep] + banner
            trimmed["_serializer_truncated"] = True
            trimmed["_truncated_chars_omitted"] = max(0, omitted)
            trimmed["_truncated"] = True
            blob2 = json.dumps(trimmed)
            if len(blob2) > max_chars:
                # Shrink the text slice until the full JSON fits.
                while keep > 0 and len(blob2) > max_chars:
                    keep = max(0, keep - 500)
                    omitted = len(result[biggest]) - keep
                    banner = _SERIALIZER_TRUNCATION_BANNER.format(omitted=max(0, omitted))
                    trimmed[biggest] = result[biggest][:keep] + banner
                    trimmed["_truncated_chars_omitted"] = max(0, omitted)
                    blob2 = json.dumps(trimmed)
            if len(blob2) <= max_chars:
                return blob2
            blob = blob2
    return blob[:max_chars]  # last resort: non-dict result, or single huge field


def fit_messages_to_budget(
    messages: list[dict[str, Any]], budget_tokens: int
) -> tuple[list[dict[str, Any]], int]:
    """Return a conversation trimmed to fit ``budget_tokens`` for the OUTGOING request, by eliding the OLDEST tool-result messages first (content -> a short placeholder). System, user and assistant messages (the source material, the instructions, and the evolving draft) are never touched. Returns ``(trimmed_messages, post-trim token estimate)`` -- the estimate reflects what the trimmed list actually contains, i.e. what will actually be SENT.

    Returns a NEW list; ``messages`` and its dicts are never mutated. Only the
    handful of tool messages actually elided get a fresh copy-on-write dict in
    the returned list -- every other entry is the SAME object reference as in
    ``messages``, so this stays cheap even for a long conversation. Eliding
    rather than mid-cutting keeps each tool message valid JSON.

    Callers that hold `messages` because it's also the live, accumulating
    debug transcript (e.g. `debug["messages"]`) must keep appending to and
    reading from the ORIGINAL `messages` list, not this function's return
    value -- the return value exists purely to build one outgoing request and
    is never persisted. Mutating it back onto the original would re-corrupt
    the stored transcript with a placeholder for content the model actually
    saw (see llm_openai_compatible.py's `_OpenAIToolLoopAdapter.send_round`).
    """
    total = estimate_message_tokens(messages)
    if total <= budget_tokens:
        return messages, total
    trimmed = list(messages)
    for i, m in enumerate(messages):
        if total <= budget_tokens:
            break
        if m.get("role") != "tool":
            continue
        content = m.get("content")
        if not isinstance(content, str) or content == _ELIDED:
            continue
        freed = estimate_tokens(content) - estimate_tokens(_ELIDED)
        if freed <= 0:
            continue
        elided = dict(m)
        elided["content"] = _ELIDED
        trimmed[i] = elided
        total -= freed
    return trimmed, total
