"""Gemini provider: the one LLMProvider implementation that can't share OpenAICompatibleProvider's wire format.

Gemini's native API uses `contents`/`parts` instead of `messages`, a
`functionCall`/`functionResponse` part shape instead of OpenAI's
`tool_calls`/`tool` messages, `role: "model"` instead of `"assistant"`, and
`usageMetadata.{prompt,candidates,total}TokenCount` instead of
`usage.{prompt,completion,total}_tokens` -- so this is real translation code,
not a config-parameterized subclass of the OpenAI-compatible base the way
Mistral/DeepSeek/OpenAI/Kimi/GLM are.

Scope note: this covers the core mechanics compose_runner.compose() actually
needs -- message/tool translation, the agentic round loop with real retry/
backoff, and usage accounting -- verified against a mocked response shape
(no live Gemini access configured in this environment yet). It deliberately
does not yet replicate every refinement OpenAICompatibleProvider has grown
over time (the live per-round budget note, the varying-argument tool-call
cap, the bogus-tool-call salvage path) -- those are follow-ups once real
Gemini traffic is actually flowing and any of them turns out to matter here
too. Gemini's functionCall parts have no `id` field at all (unlike OpenAI's
tool_calls), so the DeepSeek-style missing-tool-call-id failure class this
session hit and fixed elsewhere does not apply to this wire format.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.core.config import (
    GEMINI_API_BASE,
    GEMINI_API_KEY,
    GEMINI_MODEL_WRITER,
    LLM_MAX_TOOL_ROUNDS,
)
from app.modules.ai.llm_provider import LLMCreditError, LLMError, LLMProvider
from app.modules.ai.llm_rate_limit import throttle_llm_call
from app.modules.ai.mistral_credit_guard import is_credit_exhausted, mark_credit_exhausted
from app.modules.ai.story_spike import StorySpikedError

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 5.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _openai_messages_to_gemini_contents(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Split an OpenAI-style message list into (system_instruction_text, gemini_contents). Gemini takes the system prompt as a separate top-level field, not a "system"-role content entry."""
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(str(m["content"]))
            continue
        if role == "tool":
            contents.append(
                {
                    "role": "function",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": m.get("name") or "tool",
                                "response": {"result": _safe_parse(m.get("content"))},
                            }
                        }
                    ],
                }
            )
            continue
        gemini_role = "model" if role == "assistant" else "user"
        parts: list[dict[str, Any]] = []
        tool_calls = m.get("tool_calls") or []
        for tc in tool_calls:
            fn = tc.get("function") or {}
            parts.append(
                {
                    "functionCall": {
                        "name": fn.get("name", ""),
                        "args": _safe_parse(fn.get("arguments")) or {},
                    }
                }
            )
        content_text = m.get("content")
        if content_text:
            parts.append({"text": str(content_text)})
        if not parts:
            parts = [{"text": ""}]
        contents.append({"role": gemini_role, "parts": parts})
    return "\n\n".join(system_parts), contents


def _safe_parse(raw: object) -> object:
    """Parse a JSON-string tool argument/result back into a dict/list, or pass through unchanged if it already isn't a string (or isn't valid JSON)."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def _openai_tools_to_gemini(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI's [{"type": "function", "function": {...}}] -> Gemini's [{"functionDeclarations": [...]}]  (one block, all declarations)."""
    declarations = []
    for t in tools:
        fn = t.get("function") or {}
        declarations.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return [{"functionDeclarations": declarations}] if declarations else []


class GeminiProvider(LLMProvider):
    """Google Gemini via its native generateContent API."""

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout: float | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """Wire credentials/model/timeout, defaulting to config."""
        self._api_key = (api_key if api_key is not None else GEMINI_API_KEY).strip()
        self._api_base = (api_base if api_base is not None else GEMINI_API_BASE).rstrip("/")
        self._model = model if model is not None else GEMINI_MODEL_WRITER
        self._timeout = float(timeout) if timeout is not None else 120.0
        self._usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0}

    def usage_totals(self) -> dict[str, int]:
        """Cumulative {prompt_tokens, completion_tokens, total_tokens} across every request this instance has made."""
        return dict(self._usage)

    @property
    def model(self) -> str:
        """The model this instance is configured for."""
        return self._model

    @property
    def provider(self) -> str:
        """Always "gemini" for this class."""
        return "gemini"

    def _record_usage(self, usage_metadata: dict[str, Any]) -> None:
        prompt = int(usage_metadata.get("promptTokenCount", 0) or 0)
        completion = int(usage_metadata.get("candidatesTokenCount", 0) or 0)
        total = int(usage_metadata.get("totalTokenCount", prompt + completion) or 0)
        self._usage["prompt_tokens"] += prompt
        self._usage["completion_tokens"] += completion
        self._usage["total_tokens"] += total

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST one generateContent request, retrying on 429/5xx/network errors with exponential backoff. Returns the parsed JSON body or raises LLMError/LLMCreditError."""
        if is_credit_exhausted("gemini"):
            raise LLMCreditError("gemini credit exhausted (cached -- will retry after reset)")
        url = f"{self._api_base}/models/{self._model}:generateContent"
        headers = {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}
        for attempt in range(_MAX_RETRIES + 1):
            throttle_llm_call()
            last_attempt = attempt >= _MAX_RETRIES
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                if last_attempt:
                    raise LLMError(f"Gemini request failed after {attempt + 1} attempts: {exc}") from exc
                delay = _BACKOFF_BASE_SECONDS * (2**attempt)
                logger.warning("Gemini network error (attempt %d/%d); backing off %.1fs", attempt + 1, _MAX_RETRIES + 1, delay)
                time.sleep(delay)
                continue
            if resp.status_code in (401, 402, 403):
                mark_credit_exhausted("gemini")
                raise LLMCreditError(f"Gemini API {resp.status_code}: {resp.text[:500]}")
            if resp.status_code in _RETRYABLE_STATUS:
                if last_attempt:
                    raise LLMError(f"Gemini API {resp.status_code} after {attempt + 1} attempts: {resp.text[:500]}")
                delay = _BACKOFF_BASE_SECONDS * (2**attempt)
                logger.warning("Gemini retryable status %d (attempt %d/%d); backing off %.1fs", resp.status_code, attempt + 1, _MAX_RETRIES + 1, delay)
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise LLMError(f"Gemini API {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            self._record_usage(data.get("usageMetadata") or {})
            return data
        raise LLMError("Gemini request retry loop exhausted")  # unreachable

    def _extract_candidate_parts(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            return data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected Gemini response shape: {exc}") from exc

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.3,
    ) -> str:
        """A single plain-text completion, no tools."""
        system_text, contents = _openai_messages_to_gemini_contents(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        data = self._post(payload)
        parts = self._extract_candidate_parts(data)
        return "".join(p.get("text", "") for p in parts if "text" in p)

    def chat_json_object(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """A single completion asked to return application/json, parsed before returning."""
        system_text, contents = _openai_messages_to_gemini_contents(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "response_mime_type": "application/json",
            },
        }
        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        data = self._post(payload)
        parts = self._extract_candidate_parts(data)
        raw = "".join(p.get("text", "") for p in parts if "text" in p)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMError(f"Gemini returned non-JSON content: {raw[:200]}") from exc

    def _round_payload(
        self,
        contents: list[dict[str, Any]],
        *,
        gemini_tools: list[dict[str, Any]],
        system_text: str,
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        if gemini_tools:
            payload["tools"] = gemini_tools
        return payload

    def _execute_function_calls(
        self,
        function_calls: list[dict[str, Any]],
        *,
        handlers: dict[str, Any],
        trace: list[dict[str, Any]] | None,
        require_tool: str | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Run every function call from one round, return the functionResponse content entries to append plus whether require_tool was satisfied this round."""
        response_contents: list[dict[str, Any]] = []
        satisfied = False
        for fc in function_calls:
            name = fc.get("name", "")
            args = fc.get("args") or {}
            handler = handlers.get(name)
            try:
                result = handler(**args) if handler else {"error": f"unknown tool {name}"}
            except StorySpikedError as spike:
                # The writer aborting the article (abort_article tool) -- must
                # propagate uncaught, mirroring OpenAICompatibleProvider's
                # _run_tool_call, so the compose actually terminates instead
                # of being swallowed into a tool-error the model can shrug
                # off and keep writing past.
                if trace is not None:
                    trace.append(
                        {
                            "tool": name,
                            "arguments": args,
                            "result": {
                                "spiked": True,
                                "category": spike.category,
                                "reason": spike.reason,
                            },
                        }
                    )
                raise
            except Exception as exc:  # tool failure must not abort the article
                result = {"error": str(exc)}
            if trace is not None:
                trace.append({"tool": name, "arguments": args, "result": result})
            if name == require_tool:
                satisfied = True
            response_contents.append(
                {
                    "role": "function",
                    "parts": [{"functionResponse": {"name": name, "response": {"result": result}}}],
                }
            )
        return response_contents, satisfied

    def _finalize_exhausted(
        self, contents: list[dict[str, Any]], *, system_text: str, temperature: float
    ) -> str:
        """Out of rounds: ask once more, no tools, for a final write-up -- mirrors OpenAICompatibleProvider's own exhaustion behavior. Uses the accumulated `contents` (full round history), not a fresh context, so the wrap-up is grounded in everything already found."""
        contents.append({"role": "user", "parts": [{"text": "Now write the final JSON article."}]})
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "response_mime_type": "application/json"},
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        data = self._post(payload)
        parts = self._extract_candidate_parts(data)
        return "".join(p.get("text", "") for p in parts if "text" in p)

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
        """The agentic tool-calling loop, translated to/from Gemini's functionCall/functionResponse shape. See module docstring for what this deliberately doesn't yet replicate from OpenAICompatibleProvider."""
        del context_tokens, show_round_budget  # not yet implemented for Gemini, see module docstring
        rounds = max_rounds if max_rounds is not None else LLM_MAX_TOOL_ROUNDS
        system_text, contents = _openai_messages_to_gemini_contents(messages)
        gemini_tools = _openai_tools_to_gemini(tools)
        last_text = ""
        required_satisfied = require_tool is None

        for round_idx in range(rounds):
            payload = self._round_payload(
                contents,
                gemini_tools=gemini_tools,
                system_text=system_text,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            data = self._post(payload)
            parts = self._extract_candidate_parts(data)
            function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
            text_parts = [p.get("text", "") for p in parts if "text" in p]
            last_text = "".join(text_parts) or last_text

            if debug is not None:
                debug.setdefault("messages", []).append(
                    {"role": "assistant", "content": last_text if not function_calls else ""}
                )
                debug["rounds"] = round_idx + 1

            if not function_calls:
                if required_satisfied:
                    return last_text
                contents.append({"role": "model", "parts": parts})
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    f"Before finishing you MUST call the `{require_tool}` "
                                    "tool once on your current draft and address its "
                                    "feedback. Do that now, then output the final answer."
                                )
                            }
                        ],
                    }
                )
                required_satisfied = True  # only nudge once
                if callable(on_round):
                    on_round()
                continue

            contents.append({"role": "model", "parts": parts})
            response_contents, satisfied = self._execute_function_calls(
                function_calls, handlers=handlers, trace=trace, require_tool=require_tool
            )
            contents.extend(response_contents)
            required_satisfied = required_satisfied or satisfied
            if callable(on_round):
                on_round()

        if debug is not None:
            debug["rounds"] = rounds
            debug["exhausted"] = True
        if not finalize_on_exhaustion:
            return last_text
        return self._finalize_exhausted(contents, system_text=system_text, temperature=temperature)
