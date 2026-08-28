"""Anthropic provider: Claude Sonnet 5 via the native Messages API.

Anthropic's wire format differs from OpenAI's in the same broad way Gemini's
does (see llm_gemini_provider.py's module docstring for why that means real
translation code, not a config-parameterized OpenAICompatibleProvider
subclass): a top-level `system` string separate from `messages`, content as
a list of typed blocks (`text`/`tool_use`/`tool_result`) rather than a plain
string plus a `tool_calls` sibling field, `usage.{input,output}_tokens`
instead of `usage.{prompt,completion}_tokens`, and `max_tokens` is a
*required* request field (no default) rather than optional.

Unlike Gemini's functionCall (no id at all) and unlike the DeepSeek quirk
this session hit and fixed elsewhere (tool_calls with no id), Anthropic's
tool_use blocks DO carry an id, and tool_result blocks must reference it via
tool_use_id to pair correctly -- closer to OpenAI's tool_call_id than
Gemini's positional matching. When translating from an OpenAI-shaped
tool_call that has no id (e.g. history that originated from a DeepSeek
research pass), a synthetic id is generated so the request is never sent
with the id missing.

Scope note: same as GeminiProvider -- covers the core mechanics
compose_runner.compose() needs, verified against a mocked response shape (no
live Anthropic access configured in this environment). The agentic
chat_with_tools loop's round-budget bookkeeping, seen-calls dedup cache,
tool_call_counts/cap enforcement, require_tool nudging, exhaustion handling,
and trace/debug recording are shared with every other provider via
llm_tool_loop.run_tool_loop -- this module still owns 100% of the actual
Messages API request/response shaping (`_AnthropicToolLoopAdapter`, below).
It does not yet replicate OpenAICompatibleProvider's bogus-tool-call salvage
path or its context-window trimming -- those are about that one wire
format's own failure modes, not something Anthropic's shape has hit yet.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from app.core.config import (
    ANTHROPIC_API_BASE,
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL_WRITER,
    LLM_MAX_TOKENS,
)
from app.modules.ai.llm_provider import LLMCreditError, LLMError, LLMProvider
from app.modules.ai.llm_rate_limit import throttle_llm_call
from app.modules.ai.llm_tool_loop import (
    NormalizedToolCall,
    RoundResult,
    ToolLoopAdapter,
    run_tool_loop,
)
from app.modules.ai.mistral_credit_guard import is_credit_exhausted, mark_credit_exhausted

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 5.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_ANTHROPIC_VERSION = "2023-06-01"


def _openai_messages_to_anthropic(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Split an OpenAI-style message list into (system_text, anthropic_messages). Anthropic takes the system prompt as a separate top-level field, not a "system"-role message."""
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(str(m["content"]))
            continue
        if role == "tool":
            tool_use_id = m.get("tool_call_id") or m.get("name") or "unknown"
            anthropic_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": str(m.get("content") or ""),
                        }
                    ],
                }
            )
            continue
        blocks: list[dict[str, Any]] = []
        content_text = m.get("content")
        if content_text:
            blocks.append({"type": "text", "text": str(content_text)})
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                    "name": fn.get("name", ""),
                    "input": _safe_parse(fn.get("arguments")) or {},
                }
            )
        if not blocks:
            blocks = [{"type": "text", "text": ""}]
        anthropic_role = "assistant" if role == "assistant" else "user"
        anthropic_messages.append({"role": anthropic_role, "content": blocks})
    return "\n\n".join(system_parts), anthropic_messages


def _safe_parse(raw: object) -> object:
    """Parse a JSON-string tool argument back into a dict/list, or pass through unchanged if it already isn't a string (or isn't valid JSON)."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def _openai_tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI's [{"type": "function", "function": {...}}] -> Anthropic's [{"name", "description", "input_schema"}, ...]."""
    declarations = []
    for t in tools:
        fn = t.get("function") or {}
        declarations.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return declarations


class AnthropicProvider(LLMProvider):
    """Anthropic Claude via the native Messages API."""

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout: float | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """Wire credentials/model/timeout, defaulting to Anthropic's own config."""
        self._api_key = (api_key if api_key is not None else ANTHROPIC_API_KEY).strip()
        self._api_base = (api_base if api_base is not None else ANTHROPIC_API_BASE).rstrip("/")
        self._model = model if model is not None else ANTHROPIC_MODEL_WRITER
        self._timeout = float(timeout) if timeout is not None else 120.0
        self._usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
        }

    def usage_totals(self) -> dict[str, int]:
        """Cumulative {prompt_tokens, completion_tokens, total_tokens} across every request this instance has made."""
        return dict(self._usage)

    @property
    def model(self) -> str:
        """The model this instance is configured for."""
        return self._model

    @property
    def provider(self) -> str:
        """Always "anthropic" for this class."""
        return "anthropic"

    def _record_usage(self, usage: dict[str, Any]) -> None:
        prompt = int(usage.get("input_tokens", 0) or 0)
        completion = int(usage.get("output_tokens", 0) or 0)
        self._usage["prompt_tokens"] += prompt
        self._usage["completion_tokens"] += completion
        self._usage["total_tokens"] += prompt + completion

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST one messages request, retrying on 429/5xx/network errors with exponential backoff. Returns the parsed JSON body or raises LLMError/LLMCreditError."""
        if is_credit_exhausted("anthropic"):
            raise LLMCreditError("anthropic credit exhausted (cached -- will retry after reset)")
        url = f"{self._api_base}/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        for attempt in range(_MAX_RETRIES + 1):
            throttle_llm_call()
            last_attempt = attempt >= _MAX_RETRIES
            try:
                from app.core.http_client import get_http_client

                resp = get_http_client(timeout=self._timeout).post(
                    url, headers=headers, json=payload
                )
            except httpx.RequestError as exc:
                if last_attempt:
                    raise LLMError(
                        f"Anthropic request failed after {attempt + 1} attempts: {exc}"
                    ) from exc
                delay = _BACKOFF_BASE_SECONDS * (2**attempt)
                logger.warning(
                    "Anthropic network error (attempt %d/%d); backing off %.1fs",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    delay,
                )
                time.sleep(delay)
                continue
            if resp.status_code == 401:
                mark_credit_exhausted("anthropic")
                raise LLMCreditError(f"Anthropic API 401: {resp.text[:500]}")
            if resp.status_code in _RETRYABLE_STATUS:
                if last_attempt:
                    raise LLMError(
                        f"Anthropic API {resp.status_code} after {attempt + 1} attempts: "
                        f"{resp.text[:500]}"
                    )
                delay = _BACKOFF_BASE_SECONDS * (2**attempt)
                logger.warning(
                    "Anthropic retryable status %d (attempt %d/%d); backing off %.1fs",
                    resp.status_code,
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    delay,
                )
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise LLMError(f"Anthropic API {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            self._record_usage(data.get("usage") or {})
            return data
        raise LLMError("Anthropic request retry loop exhausted")  # unreachable

    def _effective_max_tokens(self, requested: int | None) -> int:
        """Anthropic requires max_tokens on every request -- no default like OpenAI/Gemini."""
        return requested if requested is not None else LLM_MAX_TOKENS

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.3,
    ) -> str:
        """A single plain-text completion, no tools."""
        del (
            temperature
        )  # see _round_payload's docstring: Claude Sonnet 5 rejects this field outright
        system_text, anthropic_messages = _openai_messages_to_anthropic(messages)
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": anthropic_messages,
            "max_tokens": self._effective_max_tokens(max_tokens),
        }
        if system_text:
            payload["system"] = system_text
        data = self._post(payload)
        return "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )

    def chat_json_object(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """A single completion asked (via an appended instruction, Anthropic has no dedicated JSON-mode flag) to return JSON, parsed before returning."""
        nudge = {
            "role": "user",
            "content": "Respond with ONLY a single valid JSON object, no other text.",
        }
        raw = self.chat_completion(
            [*messages, nudge], max_tokens=max_tokens, temperature=temperature
        )
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMError(f"Anthropic returned non-JSON content: {raw[:200]}") from exc

    def _round_payload(
        self,
        anthropic_messages: list[dict[str, Any]],
        *,
        anthropic_tools: list[dict[str, Any]],
        system_text: str,
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        """Build one request payload. `temperature` is accepted (kept in the signature for interface parity with the other providers' round-payload builders) but deliberately never sent -- confirmed live 2026-08-14 that Claude Sonnet 5 rejects the request outright with "`temperature` is deprecated for this model" (400 invalid_request_error) if it's present at all, not just out of range."""
        del temperature
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": anthropic_messages,
            "max_tokens": self._effective_max_tokens(max_tokens),
        }
        if system_text:
            payload["system"] = system_text
        if anthropic_tools:
            payload["tools"] = anthropic_tools
        return payload

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        handlers: dict[str, Any],
        max_rounds: int | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.6,
        trace: list[dict[str, Any]] | None = None,
        debug: dict[str, Any] | None = None,
        require_tool: str | None = None,
        context_tokens: int | None = None,
        finalize_on_exhaustion: bool = True,
        on_round: Callable[[], None] | None = None,
        show_round_budget: bool = False,
    ) -> str:
        """The agentic tool-calling loop, translated to/from Anthropic's tool_use/tool_result content-block shape. Round bookkeeping (dedup, cap, require_tool, exhaustion, trace/debug) is the shared llm_tool_loop driver -- see module docstring for what this provider's adapter still doesn't replicate from OpenAICompatibleProvider."""
        del context_tokens  # not yet implemented for Anthropic, see module docstring
        adapter = _AnthropicToolLoopAdapter(self, messages, tools)
        return run_tool_loop(
            adapter,
            tools=tools,
            handlers=handlers,
            max_rounds=max_rounds,
            max_tokens=max_tokens,
            temperature=temperature,
            trace=trace,
            debug=debug,
            require_tool=require_tool,
            finalize_on_exhaustion=finalize_on_exhaustion,
            on_round=on_round,
            show_round_budget=show_round_budget,
        )


class _AnthropicToolLoopAdapter(ToolLoopAdapter):
    """ToolLoopAdapter for AnthropicProvider's Messages API tool_use/tool_result content-block shape.

    Translates `messages`/`tools` to Anthropic's native shape once, up front
    (`_openai_messages_to_anthropic`/`_openai_tools_to_anthropic`), then owns
    folding each round's assistant turn / tool results / require_tool nudge
    back into that same running `self._messages` list.
    """

    def __init__(
        self,
        provider: AnthropicProvider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> None:
        self._provider = provider
        self._system_text, self._messages = _openai_messages_to_anthropic(messages)
        self._tools = _openai_tools_to_anthropic(tools)

    def prepare(self, debug: dict[str, Any] | None) -> None:
        if debug is not None:
            debug["messages"] = self._messages
            debug["model"] = self._provider.model

    def send_round(
        self,
        *,
        tools: list[dict[str, Any]],
        temperature: float,
        max_tokens: int | None,
        round_budget_note: str,
    ) -> RoundResult:
        # `tools` is unchanged from __init__'s translation (self._tools);
        # round-budget notes aren't implemented for Anthropic yet -- see
        # this module's docstring.
        del tools, round_budget_note
        payload = self._provider._round_payload(
            self._messages,
            anthropic_tools=self._tools,
            system_text=self._system_text,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        data = self._provider._post(payload)
        content = data.get("content", [])
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        normalized = [
            NormalizedToolCall(
                id=tu.get("id", ""), name=tu.get("name", ""), args=tu.get("input") or {}
            )
            for tu in tool_uses
        ]
        return RoundResult(text=text, tool_calls=normalized, raw=content)

    def append_assistant_turn(self, round_result: RoundResult) -> None:
        self._messages.append({"role": "assistant", "content": round_result.raw})

    def append_tool_results(self, entries: list[tuple[NormalizedToolCall, dict[str, Any]]]) -> None:
        tool_results = [
            {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(result)}
            for call, result in entries
        ]
        self._messages.append({"role": "user", "content": tool_results})

    def append_require_tool_nudge(self, require_tool: str) -> None:
        self._messages.append(
            {
                "role": "user",
                "content": (
                    f"Before finishing you MUST call the `{require_tool}` tool "
                    "once on your current draft and address its feedback. Do "
                    "that now, then output the final answer."
                ),
            }
        )

    def finalize(self, *, temperature: float, max_tokens: int | None) -> str:
        # Matches the pre-refactor exhaustion behavior exactly: Anthropic's
        # final write-up completion does NOT honor the caller's own
        # max_tokens (unlike OpenAICompatibleProvider's) -- see this
        # module's own docstring; preserved as-is, not changed here.
        del max_tokens
        self._messages.append({"role": "user", "content": "Now write the final JSON article."})
        payload = self._provider._round_payload(
            self._messages,
            anthropic_tools=[],
            system_text=self._system_text,
            temperature=temperature,
            max_tokens=None,
        )
        data = self._provider._post(payload)
        return "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
