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
    MISTRAL_MODEL_RESEARCH,
    MISTRAL_MODEL_WRITER,
    MISTRAL_REASONING_EFFORT,
    MISTRAL_TIMEOUT_SECONDS,
    MISTRAL_TOOL_RESULT_MAX_CHARS,
)
from app.modules.ai.mistral_rate_limit import throttle_mistral
from app.modules.ai.story_spike import StorySpikedError
from app.modules.ai.token_budget import fit_messages_to_budget, serialize_tool_result

logger = logging.getLogger(__name__)

# HTTP statuses worth retrying: 429 (rate limit) plus transient server errors.
# Other 4xx are client errors that won't change on retry.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _strip_markdown_json_fence(raw: str) -> str:
    """Drop ```json / ``` wrappers the model adds despite json_object mode."""
    s = raw.strip()
    if not s.startswith("```"):
        return s
    first_nl = s.find("\n")
    if first_nl == -1:
        return s
    body = s[first_nl + 1 :]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3].rstrip()
    return body


def _balanced_object_span(raw: str) -> str | None:
    """First top-level `{...}` span, respecting strings — safer than rfind('}')."""
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Parse a model reply as a JSON object, salvaging fences and prose wrappers.

    None when nothing object-like parses."""
    candidates: list[str] = []
    stripped = raw.strip()
    if stripped:
        candidates.append(stripped)
    unfenced = _strip_markdown_json_fence(stripped)
    if unfenced and unfenced not in candidates:
        candidates.append(unfenced)
    for blob in list(candidates):
        span = _balanced_object_span(blob)
        if span and span not in candidates:
            candidates.append(span)
    span = _balanced_object_span(stripped)
    if span and span not in candidates:
        candidates.append(span)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    # Scores often survive when issue strings break JSON (unescaped quotes).
    import re

    narrative_m = re.search(r'"narrative_synthesis"\s*:\s*(\d+)', stripped)
    technical_m = re.search(r'"technical_depth"\s*:\s*(\d+)', stripped)
    if narrative_m or technical_m:
        out: dict[str, Any] = {"issues": []}
        if narrative_m:
            out["narrative_synthesis"] = int(narrative_m.group(1))
        if technical_m:
            out["technical_depth"] = int(technical_m.group(1))
        return out
    return None


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


class MistralCreditError(MistralError):
    """Raised on 401/402 — Mistral rejects the request outright, no retry.
    Seen in practice (2026-07-10) as 401 "Unauthorized" once monthly prepaid
    credit ran out; some providers disable the key itself rather than
    returning a billing-specific code, so this also covers a genuinely bad/
    revoked key — either way, waiting and retrying the same request won't help."""


# Live model metadata (max context length, reasoning_effort support) from
# Mistral's own GET /v1/models, cached per model name for this process's
# lifetime — refreshes naturally on every deploy/restart. Root-caused
# 2026-07-15: a hardcoded context-length comment ("mistral-small ~128k") went
# stale when Mistral silently upgraded the "-latest" alias to 262144 without
# changing the model name, and every Large-tier request was silently paying
# for two API calls (send reasoning_effort, get rejected, retry without it)
# because nothing checked the model's actual advertised capabilities. A
# module-level cache (not per-instance) since MistralClient instances are
# created fresh per compose session but the underlying model's real
# properties don't change between them.
_model_metadata_cache: dict[str, dict[str, Any]] = {}


def _fetch_model_metadata(*, api_base: str, api_key: str, model: str) -> dict[str, Any]:
    """{"max_context_length": int, "reasoning": bool} for `model`, or {} on any
    failure — callers fall back to their existing hardcoded defaults, so a
    slow/unreachable /v1/models never blocks a compose."""
    cached = _model_metadata_cache.get(model)
    if cached is not None:
        return cached
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{api_base}/models", headers={"Authorization": f"Bearer {api_key}"})
        resp.raise_for_status()
        for m in resp.json().get("data", []):
            if m.get("id") == model:
                meta = {
                    "max_context_length": m.get("max_context_length"),
                    "reasoning": bool((m.get("capabilities") or {}).get("reasoning")),
                }
                _model_metadata_cache[model] = meta
                return meta
    except Exception:
        logger.debug("failed to fetch live metadata for model %s", model, exc_info=True)
    return {}  # not cached — a transient failure should be retried by the next instance


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
        self._usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._metadata = (
            _fetch_model_metadata(api_base=self._api_base, api_key=self._api_key, model=self._model)
            if self._api_key
            else {}
        )
        # Not every model accepts reasoning_effort (e.g. Mistral Large 3 400s with
        # "reasoning_effort is not enabled for this model"). Seeded from live
        # capabilities when available; the 400-response check below is still a
        # lazy-discovery safety net for whatever the live lookup missed or
        # couldn't reach, and stays off for every later call on this instance so
        # a multi-round session doesn't re-pay for the same rejection every round.
        self._reasoning_effort_unsupported = not self._metadata.get("reasoning", True)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def usage_totals(self) -> dict[str, int]:
        """Cumulative token usage across every request this instance has made
        (a compose session's client(s) are created fresh per session, so this
        is the session total, not a lifetime counter)."""
        return dict(self._usage)

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
            if (
                resp.status_code == 400
                and "reasoning_effort" in payload
                and "reasoning_effort" in resp.text
                and "not enabled" in resp.text
            ):
                self._reasoning_effort_unsupported = True
                payload = {k: v for k, v in payload.items() if k != "reasoning_effort"}
                logger.warning(
                    "Mistral model %s does not support reasoning_effort; retrying without it",
                    self._model,
                )
                continue
            if resp.status_code in (401, 402):
                raise MistralCreditError(
                    f"Mistral API {resp.status_code}: {resp.text[:500]}"
                )
            if resp.status_code >= 400:
                raise MistralError(f"Mistral API {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            usage = data.get("usage") or {}
            self._usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            self._usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            self._usage["total_tokens"] += int(usage.get("total_tokens") or 0)
            return data
        raise MistralError("Mistral request retry loop exhausted")  # unreachable

    def _log_task_context(self, op: str) -> None:
        """Log which Celery task is driving this Mistral call, so an unexpected
        burst of API queries can be traced back to the task that caused it."""
        try:
            from celery import current_task

            name = getattr(current_task, "name", None) or "no-celery-task"
            logger.info("Mistral %s | model=%s | celery_task=%s", op, self._model, name)
        except Exception:
            logger.debug("failed to log celery task context for Mistral %s call", op, exc_info=True)

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
        if MISTRAL_REASONING_EFFORT and not self._reasoning_effort_unsupported:
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
        parsed = _parse_json_object(raw)
        if parsed is not None:
            return parsed
        # Despite response_format=json_object the model occasionally wraps the
        # JSON in prose/fences or drifts entirely (seen on review_draft calls).
        # One corrective retry with the bad reply in context fixes most cases —
        # cheaper than failing the whole compose/revision step.
        if raw.strip():
            retry_messages = [
                *messages,
                {"role": "assistant", "content": raw[:4000]},
                {
                    "role": "user",
                    "content": "Your previous reply was not a valid JSON object. "
                    "Reply again with ONLY the JSON object — no prose, no fences.",
                },
            ]
        else:
            # An empty reply can't be echoed back: Mistral 400s on assistant
            # messages with neither content nor tool_calls. Plain re-send.
            retry_messages = messages
        raw = self.chat_completion(
            retry_messages,
            max_tokens=max_tokens,
            json_object=True,
            temperature=temperature,
        )
        parsed = _parse_json_object(raw)
        if parsed is None:
            raise MistralError(f"Mistral returned non-JSON content: {raw[:200]}")
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
        context_tokens: int | None = None,
        finalize_on_exhaustion: bool = True,
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
            # Two-stage compose invokes chat_with_tools multiple times sharing
            # one debug dict (initial research, a RESEARCH_FLOOR nudge pass,
            # a digest gap-fill pass) — prepend any prior round's transcript so
            # it survives instead of being silently overwritten by this round's
            # fresh 2-message start (previously lost every earlier round's tool
            # calls from the persisted/audited transcript, though not from
            # `trace`, which accumulates by reference regardless).
            prior = debug.get("messages")
            if isinstance(prior, list) and prior:
                convo = prior + convo
            debug["messages"] = convo  # mutated in place → full transcript
            debug["model"] = self._model
        last_content = ""
        # Guard against runaway loops: the data tools (price, market, chain head)
        # return stable data, but the model otherwise re-calls them dozens of
        # times. Cache (name+args) signatures and refuse to re-run an identical
        # call, nudging the model to write instead.
        seen_calls: set[str] = set()
        # Cross-pass dedup (2026-07-16): the research floor and gap-fill passes
        # call chat_with_tools again with a FRESH conversation but the SAME
        # shared trace — an empty cache here let a later pass re-run an earlier
        # pass's identical searches verbatim (a real RandGallery session
        # repeated 5 of its 35 calls; ~970k tokens total). Seed from the trace
        # so exact repeats get the "already called" nudge across passes too.
        # Errored calls are NOT seeded: retrying a transient failure in a later
        # pass is legitimate.
        for entry in trace or ():
            result = entry.get("result")
            if isinstance(result, dict) and result.get("error"):
                continue
            try:
                seen_calls.add(
                    f"{entry.get('tool')}:"
                    f"{json.dumps(entry.get('arguments') or {}, sort_keys=True)}"
                )
            except (TypeError, ValueError):
                continue
        # Enforce a mandatory tool (e.g. review_draft): the model is not allowed
        # to produce its final answer until it has called this tool at least once.
        required_satisfied = require_tool is None
        required_nudged = False
        response_reserve = max_tokens if max_tokens is not None else MISTRAL_MAX_TOKENS
        # Leave room for the model's reply plus a safety pad below the window.
        # An explicit context_tokens always wins; otherwise prefer this
        # instance's own live-fetched limit (correct for whatever self._model
        # actually is) over the generic hardcoded fallback.
        window = context_tokens if context_tokens is not None else (
            self._metadata.get("max_context_length") or MISTRAL_CONTEXT_TOKENS
        )
        convo_budget = window - response_reserve - MISTRAL_CONTEXT_SAFETY_TOKENS
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
            if MISTRAL_REASONING_EFFORT and not self._reasoning_effort_unsupported:
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
                    note = (
                        "You already called this tool with these exact arguments "
                        "this session; its data has not changed. Do NOT call it "
                        "again — use the result you already have and write the "
                        "article now."
                    )
                    if name == "fetch_url" and not args.get("continue_reading"):
                        note += (
                            " If you meant to read more of a long page, call fetch_url "
                            "again with the same url and continue_reading=true."
                        )
                    result = {"note": note}
                else:
                    seen_calls.add(sig)
                    handler = handlers.get(name)
                    try:
                        result = handler(**args) if handler else {"error": f"unknown tool {name}"}
                    except StorySpikedError as spike:
                        # The one tool "failure" that MUST abort the article —
                        # abort_article is the writer refusing to compose at all.
                        # Record it in the trace first so the session shows the
                        # writer's own reasoning, then let it escape the loop.
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
        # Research/gap-fill callers invoke this loop for its tool side-effects
        # (the trace) and DISCARD the return value — burning a full completion
        # asking the research model to "write the final JSON article" on
        # exhaustion was pure waste (confirmed 2026-07-14: a gap-fill pass ran
        # out of rounds and paid for an article nobody read). Those call sites
        # pass finalize_on_exhaustion=False.
        if not finalize_on_exhaustion:
            return last_content
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


def get_mistral_research_client() -> MistralClient:
    return MistralClient(model=MISTRAL_MODEL_RESEARCH)


def get_mistral_digest_client() -> MistralClient:
    return MistralClient(model=MISTRAL_MODEL_DIGEST)
