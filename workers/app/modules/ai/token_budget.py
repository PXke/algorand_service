"""Token-aware context management for the writer's agentic tool loop.

Cost is not the constraint (Mistral usage is effectively unlimited here) — the
context window is. So instead of a blind per-tool-result character cap (which cut
JSON mid-string, produced invalid payloads, and silently dropped trailing fields
like a page's `links` list), we keep tool results generous and only trim when the
WHOLE conversation approaches the model's context limit.

No tokenizer dependency is available in this environment, so token counts are
estimated from character length with a deliberately conservative ratio (we'd
rather over-count and trim a little early than overflow the window).
"""

from __future__ import annotations

import json
from typing import Any

# Mistral's Tekken tokenizer averages ~3.5-4 chars/token on English + markdown.
# We use a LOW ratio on purpose: it OVER-counts tokens, so we stay under the real
# limit rather than discovering the overflow when the API rejects the request.
_CHARS_PER_TOKEN = 3.2
# Per-message envelope (role, delimiters, tool_call ids) the API counts on top of
# the content itself.
_MESSAGE_OVERHEAD_TOKENS = 4

_ELIDED = json.dumps({"note": "[earlier tool result elided to fit the context window]"})


def estimate_tokens(text: str) -> int:
    """Rough token count for a string (no tokenizer dep). See _CHARS_PER_TOKEN."""
    if not text:
        return 0
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Approximate total tokens of a chat conversation: content + serialized
    tool-call arguments + a small per-message envelope."""
    total = 0
    for m in messages:
        total += _MESSAGE_OVERHEAD_TOKENS
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            total += estimate_tokens(str(fn.get("name", "")))
            total += estimate_tokens(str(fn.get("arguments", "")))
    return total


def serialize_tool_result(result: Any, max_chars: int) -> str:
    """JSON-serialize a tool result capped at ``max_chars`` WITHOUT mangling the
    structure: if it's too big, trim only the single longest string field (e.g. a
    page's `text`/`body`) and keep everything else, so `links`, `url`, and `title`
    still reach the model. Replaces the old ``json.dumps(result)[:N]`` that cut
    mid-string into invalid JSON and dropped trailing fields."""
    blob = json.dumps(result)
    if len(blob) <= max_chars:
        return blob
    if isinstance(result, dict):
        str_fields = [k for k, v in result.items() if isinstance(v, str) and v]
        if str_fields:
            biggest = max(str_fields, key=lambda k: len(result[k]))
            overflow = len(blob) - max_chars
            keep = max(0, len(result[biggest]) - overflow - 80)
            trimmed = dict(result)
            trimmed[biggest] = result[biggest][:keep] + "…[truncated to fit context]"
            trimmed["_truncated"] = True
            blob2 = json.dumps(trimmed)
            if len(blob2) <= max_chars:
                return blob2
            blob = blob2
    return blob[:max_chars]  # last resort: non-dict result, or single huge field


def fit_messages_to_budget(messages: list[dict[str, Any]], budget_tokens: int) -> int:
    """Shrink a conversation IN PLACE to fit ``budget_tokens`` by eliding the
    OLDEST tool-result messages first (content → a short placeholder). System,
    user and assistant messages (the source material, the instructions, and the
    evolving draft) are never touched. Returns the post-trim token estimate.

    In place so a caller holding the same list (e.g. a live debug transcript)
    stays consistent; eliding rather than mid-cutting keeps each tool message
    valid JSON."""
    total = estimate_message_tokens(messages)
    if total <= budget_tokens:
        return total
    for m in messages:
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
        m["content"] = _ELIDED
        total -= freed
    return total
