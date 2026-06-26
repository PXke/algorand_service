from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.core.config import (
    MISTRAL_API_BASE,
    MISTRAL_API_KEY,
    MISTRAL_BACKOFF_BASE_SECONDS,
    MISTRAL_BACKOFF_MAX_SECONDS,
    MISTRAL_CONTEXT_SAFETY_TOKENS,
    MISTRAL_CONTEXT_TOKENS,
    MISTRAL_MAX_RETRIES,
    MISTRAL_MAX_TOKENS,
    MISTRAL_MAX_TOOL_ROUNDS,
    MISTRAL_MODEL,
    MISTRAL_MODEL_DIGEST,
    MISTRAL_MODEL_WRITER,
    MISTRAL_REASONING_EFFORT,
    MISTRAL_TIMEOUT_SECONDS,
    MISTRAL_TOOL_RESULT_MAX_CHARS,
)
from app.modules.ai.mistral_rate_limit import throttle_mistral
from app.modules.ai.token_budget import fit_messages_to_budget, serialize_tool_result

logger = logging.getLogger(__name__)

# HTTP statuses worth retrying: 429 (rate limit) plus transient server errors.
# Other 4xx are client errors that won't change on retry.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _message_text(message: dict[str, Any]) -> str:
    """Extract assistant text from a chat message.

    Reasoning models (Small 4 at reasoning_effort != none) may return ``content``
    as a list of typed chunks (thinking + text) rather than a plain string. We
    keep only the answer text and drop thinking/reasoning chunks so the caller's
    strict-JSON parse never sees the reasoning trace."""
    content = message.get("content")
    if isinstance(content, list):
        parts = [
            str(chunk.get("text", ""))
            for chunk in content
            if isinstance(chunk, dict) and chunk.get("type") in ("text", "output_text", None)
        ]
        return "".join(parts)
    return str(content or "")


class MistralError(Exception):
    pass


class MistralRateLimitError(MistralError):
    """Raised when the API keeps returning 429 after the retry budget."""


class MistralClient:
    """Thin connector for Mistral Chat Completions (RFC 9110 HTTP JSON)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._api_key = (api_key if api_key is not None else MISTRAL_API_KEY).strip()
        self._api_base = (api_base if api_base is not None else MISTRAL_API_BASE).rstrip("/")
        self._model = model if model is not None else MISTRAL_MODEL
        self._timeout = float(timeout if timeout is not None else MISTRAL_TIMEOUT_SECONDS)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST one chat/completions request through the shared rate-limit gate,
        retrying on 429 with Retry-After / exponential backoff. Returns the
        parsed JSON body or raises MistralError."""
        url = f"{self._api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        # Retry transient failures, not just 429: server-side 5xx and network /
        # timeout errors are equally transient and were previously fatal on the
        # first try (e.g. a read timeout on the big two-stage revision call).
        for attempt in range(MISTRAL_MAX_RETRIES + 1):
            throttle_mistral()
            last_attempt = attempt >= MISTRAL_MAX_RETRIES
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                # Connection/read timeout, DNS, reset — transient transport error.
                if last_attempt:
                    raise MistralError(
                        f"Mistral request failed after {attempt + 1} attempts: {exc}"
                    ) from exc
                wait = min(MISTRAL_BACKOFF_MAX_SECONDS, MISTRAL_BACKOFF_BASE_SECONDS * (2**attempt))
                logger.warning(
                    "Mistral network error (attempt %d/%d): %s; backing off %.1fs",
                    attempt + 1, MISTRAL_MAX_RETRIES + 1, exc, wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                if last_attempt:
                    if resp.status_code == 429:
                        raise MistralRateLimitError(
                            f"Mistral API 429 after {attempt + 1} attempts: {resp.text[:300]}"
                        )
                    raise MistralError(
                        f"Mistral API {resp.status_code} after {attempt + 1} attempts: "
                        f"{resp.text[:300]}"
                    )
                wait = _retry_after_seconds(resp) or min(
                    MISTRAL_BACKOFF_MAX_SECONDS, MISTRAL_BACKOFF_BASE_SECONDS * (2**attempt)
                )
                logger.warning(
                    "Mistral %d (attempt %d/%d); backing off %.1fs",
                    resp.status_code, attempt + 1, MISTRAL_MAX_RETRIES + 1, wait,
                )
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise MistralError(f"Mistral API {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        raise MistralError("Mistral request retry loop exhausted")  # unreachable

    def _log_task_context(self, op: str) -> None:
        """Log which Celery task is driving this Mistral call, so an unexpected
        burst of API queries can be traced back to the task that caused it."""
        try:
            from celery import current_task

            name = getattr(current_task, "name", None) or "no-celery-task"
            logger.info("Mistral %s | model=%s | celery_task=%s", op, self._model, name)
        except Exception:
            pass

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        json_object: bool = True,
        temperature: float = 0.3,
    ) -> str:
        if not self._api_key:
            msg = "MISTRAL_API_KEY is not set"
            raise MistralError(msg)
        self._log_task_context("chat_completion")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else MISTRAL_MAX_TOKENS,
            "temperature": temperature,
        }
        if MISTRAL_REASONING_EFFORT:
            payload["reasoning_effort"] = MISTRAL_REASONING_EFFORT
        if json_object:
            payload["response_format"] = {"type": "json_object"}

        data = self._post(payload)
        try:
            return _message_text(data["choices"][0]["message"])
        except (KeyError, IndexError, TypeError) as exc:
            raise MistralError("unexpected Mistral response shape") from exc

    def chat_json_object(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        raw = self.chat_completion(
            messages,
            max_tokens=max_tokens,
            json_object=True,
            temperature=temperature,
        )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MistralError(f"Mistral returned non-JSON content: {raw[:200]}") from exc
        if not isinstance(parsed, dict):
            raise MistralError("Mistral JSON root must be an object")
        return parsed

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
    ) -> str:
        """Agentic loop: let the model call the provided tools, execute them,
        feed results back, and return the final assistant message content.
        Tools are real functions the writer invokes on demand (live price,
        chain stats, platform search, recent articles). Pass ``debug`` to capture
        the full transcript (it tracks ``convo`` live + records the round count)."""
        if not self._api_key:
            raise MistralError("MISTRAL_API_KEY is not set")
        self._log_task_context("chat_with_tools")

        rounds = max_rounds if max_rounds is not None else MISTRAL_MAX_TOOL_ROUNDS
        convo = list(messages)
        if debug is not None:
            debug["messages"] = convo  # mutated in place → full transcript
            debug["model"] = self._model
        last_content = ""
        # Guard against runaway loops: the data tools (price, market, chain head)
        # return stable data, but the model otherwise re-calls them dozens of
        # times. Cache (name+args) signatures and refuse to re-run an identical
        # call, nudging the model to write instead.
        seen_calls: set[str] = set()
        # Enforce a mandatory tool (e.g. review_draft): the model is not allowed
        # to produce its final answer until it has called this tool at least once.
        required_satisfied = require_tool is None
        required_nudged = False
        response_reserve = max_tokens if max_tokens is not None else MISTRAL_MAX_TOKENS
        # Leave room for the model's reply plus a safety pad below the window.
        convo_budget = MISTRAL_CONTEXT_TOKENS - response_reserve - MISTRAL_CONTEXT_SAFETY_TOKENS
        for round_idx in range(rounds):
            # Token-aware trim: keep tool results generous, but if many rounds have
            # accumulated and the conversation nears the context window, elide the
            # OLDEST tool results (in place) so the request never overflows.
            if convo_budget > 0:
                fit_messages_to_budget(convo, convo_budget)
            payload: dict[str, Any] = {
                "model": self._model,
                "messages": convo,
                "max_tokens": response_reserve,
                "temperature": temperature,
                "tools": tools,
                "tool_choice": "auto",
            }
            if MISTRAL_REASONING_EFFORT:
                payload["reasoning_effort"] = MISTRAL_REASONING_EFFORT
            data = self._post(payload)
            try:
                msg = data["choices"][0]["message"]
            except (KeyError, IndexError, TypeError) as exc:
                raise MistralError("unexpected Mistral response shape") from exc
            last_content = _message_text(msg) or last_content
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                # Model wants to finish but hasn't called the mandatory tool yet:
                # send it back once with an explicit instruction. Only nudge once
                # so a stubborn model can't loop forever.
                if not required_satisfied and not required_nudged:
                    required_nudged = True
                    convo.append(msg)
                    convo.append(
                        {
                            "role": "user",
                            "content": (
                                f"Before finishing you MUST call the `{require_tool}` tool "
                                "once on your current draft (title + full body) and address "
                                "its feedback. Do that now, then output the final JSON article."
                            ),
                        }
                    )
                    continue
                if debug is not None:
                    debug["rounds"] = round_idx + 1
                return last_content
            # Some models emit their final JSON article as a bogus tool call
            # (function name like ```json or the article itself) instead of
            # message content. Recover it so the article is not lost.
            salvaged = _salvage_final_article(tool_calls, handlers)
            if salvaged is not None:
                if debug is not None:
                    debug["rounds"] = round_idx + 1
                    debug["salvaged"] = True
                return salvaged
            convo.append(msg)
            for call in tool_calls:
                fn = (call.get("function") or {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                sig = f"{name}:{json.dumps(args, sort_keys=True)}"
                if sig in seen_calls and name != "suggest_tool":
                    # Identical call already executed this session — don't re-run
                    # the handler or resend the (unchanged) data; nudge to write.
                    result = {
                        "note": (
                            "You already called this tool with these exact arguments "
                            "this session; its data has not changed. Do NOT call it "
                            "again — use the result you already have and write the "
                            "article now."
                        )
                    }
                else:
                    seen_calls.add(sig)
                    handler = handlers.get(name)
                    try:
                        result = handler(**args) if handler else {"error": f"unknown tool {name}"}
                    except Exception as exc:  # tool failure must not abort the article
                        result = {"error": str(exc)}
                if name == require_tool:
                    required_satisfied = True
                if trace is not None:
                    trace.append({"tool": name, "arguments": args, "result": result})
                convo.append(
                    {
                        "role": "tool",
                        "name": name,
                        "tool_call_id": call.get("id", ""),
                        # Structure-preserving cap: trims only the biggest string
                        # field (e.g. page text) so links/url/title still survive,
                        # unlike the old blind json.dumps(result)[:4000].
                        "content": serialize_tool_result(result, MISTRAL_TOOL_RESULT_MAX_CHARS),
                    }
                )
        # Out of rounds: ask once more without tools for a final write-up.
        if debug is not None:
            debug["rounds"] = rounds
            debug["exhausted"] = True
        return self.chat_completion(
            [*convo, {"role": "user", "content": "Now write the final JSON article."}],
            json_object=True,
            temperature=temperature,
        )


def _salvage_final_article(
    tool_calls: list[dict[str, Any]], handlers: dict[str, Any]
) -> str | None:
    """Recover a final JSON article a model wrongly emitted as a tool call.

    Returns the article JSON string (parseable by the caller) when an *unknown*
    tool call's name or arguments contains a JSON object with title+body, else
    None. Real (known) tool calls are left to execute normally.
    """
    for call in tool_calls:
        fn = call.get("function") or {}
        name = fn.get("name", "")
        if name in handlers:
            continue
        for blob in (name, fn.get("arguments", "")):
            if not isinstance(blob, str) or "{" not in blob:
                continue
            s = blob.strip().strip("`").strip()
            if s.lower().startswith("json"):
                s = s[4:].strip()
            i, j = s.find("{"), s.rfind("}")
            if i == -1 or j <= i:
                continue
            candidate = s[i : j + 1]
            try:
                obj = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict) and obj.get("title") and obj.get("body"):
                return candidate
    return None


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Seconds to wait from a 429 response's Retry-After header (delta-seconds
    form), or None when absent/unparseable."""
    raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def get_mistral_client(*, model: str | None = None) -> MistralClient:
    return MistralClient(model=model or MISTRAL_MODEL_WRITER)


def get_mistral_digest_client() -> MistralClient:
    return MistralClient(model=MISTRAL_MODEL_DIGEST)
