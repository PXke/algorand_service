"""Thin HTTP connector for the Mistral Chat Completions API."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any, ClassVar

import httpx

from app.core.config import (
    DEEPSEEK_API_BASE,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MAX_TOKENS,
    DEEPSEEK_MODEL_DIGEST,
    DEEPSEEK_MODEL_RESEARCH,
    DEEPSEEK_MODEL_RUBRIC,
    DEEPSEEK_MODEL_TRANSLATE,
    DEEPSEEK_MODEL_WRITER,
    LLM_MAX_TOOL_ROUNDS,
    LLM_PROVIDER_DIGEST,
    LLM_PROVIDER_DIGEST_CANARY_PCT,
    LLM_PROVIDER_RESEARCH,
    LLM_PROVIDER_RESEARCH_CANARY_PCT,
    LLM_PROVIDER_RUBRIC,
    LLM_PROVIDER_RUBRIC_CANARY_PCT,
    LLM_PROVIDER_TRANSLATE,
    LLM_PROVIDER_TRANSLATE_CANARY_PCT,
    LLM_PROVIDER_WRITER,
    LLM_PROVIDER_WRITER_CANARY_PCT,
    MISTRAL_API_BASE,
    MISTRAL_API_KEY,
    MISTRAL_BACKOFF_BASE_SECONDS,
    MISTRAL_BACKOFF_MAX_SECONDS,
    MISTRAL_CONTEXT_SAFETY_TOKENS,
    MISTRAL_CONTEXT_TOKENS,
    MISTRAL_MAX_RETRIES,
    MISTRAL_MAX_TOKENS,
    MISTRAL_MODEL,
    MISTRAL_MODEL_DIGEST,
    MISTRAL_MODEL_RESEARCH,
    MISTRAL_MODEL_TRANSLATE,
    MISTRAL_MODEL_WRITER,
    MISTRAL_REASONING_EFFORT,
    MISTRAL_TIMEOUT_SECONDS,
    MISTRAL_TOOL_RESULT_MAX_CHARS,
)
from app.modules.ai.llm_provider import LLMCreditError, LLMError, LLMProvider
from app.modules.ai.llm_rate_limit import throttle_llm_call
from app.modules.ai.mistral_credit_guard import is_credit_exhausted, mark_credit_exhausted
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


def _string_scan_step(ch: str, *, in_string: bool, escape: bool) -> tuple[bool, bool]:
    """Advance the quoted-string tracking state by one character. Returns (in_string, escape)."""
    if in_string:
        if escape:
            return in_string, False
        if ch == "\\":
            return in_string, True
        if ch == '"':
            return False, False
        return in_string, escape
    if ch == '"':
        return True, False
    return in_string, escape


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
        was_in_string = in_string
        in_string, escape = _string_scan_step(ch, in_string=in_string, escape=escape)
        if was_in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None


def _json_object_candidates(stripped: str) -> list[str]:
    """Ordered, deduped candidate substrings worth trying as the model's JSON object: the raw reply, its fence-stripped form, and the balanced `{...}` span of each."""
    candidates: list[str] = []
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
    return candidates


def _salvage_scores_from_broken_json(stripped: str) -> dict[str, Any] | None:
    """Regex-salvage narrative_synthesis/technical_depth scores when the model's JSON itself won't parse (e.g. an unescaped quote inside an issue string) — these two numeric fields often survive even when the rest breaks."""
    import re

    narrative_m = re.search(r'"narrative_synthesis"\s*:\s*(\d+)', stripped)
    technical_m = re.search(r'"technical_depth"\s*:\s*(\d+)', stripped)
    if not narrative_m and not technical_m:
        return None
    out: dict[str, Any] = {"issues": []}
    if narrative_m:
        out["narrative_synthesis"] = int(narrative_m.group(1))
    if technical_m:
        out["technical_depth"] = int(technical_m.group(1))
    return out


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Parse a model reply as a JSON object, salvaging fences and prose wrappers.

    None when nothing object-like parses.
    """
    stripped = raw.strip()
    for candidate in _json_object_candidates(stripped):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return _salvage_scores_from_broken_json(stripped)


def _message_text(message: dict[str, Any]) -> str:
    """Extract assistant text from a chat message.

    Reasoning models (Small 4 at reasoning_effort != none) may return ``content``
    as a list of typed chunks (thinking + text) rather than a plain string. We
    keep only the answer text and drop thinking/reasoning chunks so the caller's
    strict-JSON parse never sees the reasoning trace.
    """
    content = message.get("content")
    if isinstance(content, list):
        parts = [
            str(chunk.get("text", ""))
            for chunk in content
            if isinstance(chunk, dict) and chunk.get("type") in ("text", "output_text", None)
        ]
        return "".join(parts)
    return str(content or "")


def _ensure_tool_call_ids(tool_calls: list[dict[str, Any]]) -> None:
    """Backfill a synthetic `id` and a missing `type` (mutated in place) on any tool_call that lacks them.

    Root-caused 2026-08-13: DeepSeek's tool_calls responses, as returned to
    this client, come back with NO `id` field at all on every single call —
    confirmed live against a stored compose_sessions transcript (model
    deepseek-v4-flash, every assistant tool_call and its paired tool-result's
    tool_call_id empty). That's harmless within a single provider's own
    multi-round loop (each round only needs internal consistency, which
    `_run_tool_call`'s `call.get("id", "")` fallback preserves). It breaks
    the moment this history is later echoed into a call served by Mistral's
    stricter API (e.g. the revision pass, `_merged_convo_with_prior_debug`
    prepending an earlier DeepSeek-run stage's transcript into a Mistral
    request): Mistral rejects it outright with "messages[N]: missing field
    `id`" — every one of 5 real LumiRogue recompose attempts hit this and
    silently lost the revision-tool-call pass. Assigning an id here, at the
    moment a tool_calls list is first received, keeps it consistent across
    every later use of the SAME list object (the echoed assistant message
    via `_for_conversation_history` and the tool-result's tool_call_id both
    read from these same dicts) regardless of which provider serves a later
    round.

    `type` backfilled for the identical reason, confirmed live 2026-08-14:
    OpenAI's stricter API rejects a replayed tool_calls entry missing `type`
    with "Missing required parameter: 'messages[N].tool_calls[0].type'" --
    same failure shape as the `id` gap (a field some upstream provider's raw
    response omits, that a later-stage call served by a stricter provider
    then chokes on), so it gets the same fix at the same defensive point.
    """
    for call in tool_calls:
        if not call.get("id"):
            call["id"] = f"call_{uuid.uuid4().hex[:24]}"
        if not call.get("type"):
            call["type"] = "function"


def _for_conversation_history(message: dict[str, Any]) -> dict[str, Any]:
    """A copy of an assistant message safe to append to `convo` for resending in later rounds — strips `reasoning_content` (DeepSeek's separate thinking-trace field, sibling to `content`).

    Root-caused 2026-08-06: a multi-round tool-calling loop appended the RAW
    API response message into convo every round, including reasoning_content
    — so round 2 resent round 1's full reasoning trace, round 3 resent both,
    and so on, compounding across rounds within one loop. A real special-
    edition session (many rounds, plus its own extra gap-fill loop) hit 4.6M
    cumulative prompt tokens this way before failing outright. Reasoning is
    meant to inform THIS round's decision, not be replayed as if the model
    already said it out loud in a prior turn — stripping it costs nothing
    (each round still reasons fresh) and removes the compounding entirely.
    """
    if "reasoning_content" not in message:
        return message
    return {k: v for k, v in message.items() if k != "reasoning_content"}


# LLMError/LLMCreditError (llm_provider.py) are the canonical exception names
# for the whole multi-provider abstraction -- llm_anthropic_provider.py and
# llm_gemini_provider.py already raise them directly. MistralError/
# MistralCreditError are kept as literal aliases (not separate classes) so
# every existing `except MistralError`/`isinstance(exc, MistralCreditError)`
# call site (30+, see the mistral_* -> llm_* rename task) keeps working
# unchanged, while also fixing what was, until this alias, a real gap: those
# call sites could not catch a failure raised by the Anthropic/Gemini
# providers, only by this OpenAI-compatible one, because the two hierarchies
# were disconnected Exception subclasses with no relationship to each other.
MistralError = LLMError
MistralCreditError = LLMCreditError


class MistralRateLimitError(MistralError):
    """Raised when the API keeps returning 429 after the retry budget."""


class PeakHoursBlockedError(MistralError):
    """Raised by article_composer's peak-hours guard (2026-08-15) -- NOT a real API failure, deliberately not renamed off "Mistral" yet (see the mistral_* -> llm_* rename task) so it's caught by every existing `except MistralError` call site with zero changes there.

    Every one of those sites must check `isinstance(exc, PeakHoursBlockedError)`
    BEFORE falling through to the generic "mistral_failed" branch: unlike a
    real failure, this is an intentional, expected, routine skip (we chose
    not to call the API), so it must never log at ERROR level or report a
    status that reads as something being broken.
    """


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


def _fetch_model_metadata(
    *, api_base: str, api_key: str, model: str, provider: str = "mistral"
) -> dict[str, Any]:
    """{"max_context_length": int, "reasoning": bool} for `model`, or {} on any failure — callers fall back to their existing hardcoded defaults, so a slow/unreachable /v1/models never blocks a compose."""
    cached = _model_metadata_cache.get(model)
    if cached is not None:
        return cached
    if is_credit_exhausted(provider):
        return {}
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


class MistralClient(LLMProvider):
    """Thin connector for Mistral Chat Completions (RFC 9110 HTTP JSON).

    Also serves DeepSeek today (both speak the same OpenAI-compatible wire
    format -- see `provider`) and, as of 2026-08-14, is the shared
    implementation behind every OpenAI-compatible provider in
    llm_openai_compatible.py (OpenAICompatibleProvider = this class; Mistral/
    DeepSeek/OpenAI/Kimi/GLM are thin subclasses supplying their own
    api_key/api_base/model defaults) -- kept here, not yet physically moved,
    pending the mistral_* -> llm_* rename.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        provider: str = "mistral",
    ) -> None:
        """Wire credentials/model/timeout, defaulting to config, and fetch live model metadata. `provider` is a label only ("mistral"/"deepseek") — it doesn't change the wire format, just which credit-exhaustion breaker (mistral_credit_guard) this instance's 401/402s trip, so a dead key on one provider can't silently short-circuit the other's calls too."""
        self._provider = provider
        self._api_key = (api_key if api_key is not None else MISTRAL_API_KEY).strip()
        self._api_base = (api_base if api_base is not None else MISTRAL_API_BASE).rstrip("/")
        self._model = model if model is not None else MISTRAL_MODEL
        self._timeout = float(timeout if timeout is not None else MISTRAL_TIMEOUT_SECONDS)
        self._usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._metadata = (
            _fetch_model_metadata(
                api_base=self._api_base,
                api_key=self._api_key,
                model=self._model,
                provider=self._provider,
            )
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
        # One instance == one compose session's worth of calls on one role
        # (see usage_totals' docstring above), so a per-instance key is
        # exactly the right granularity to pin every round's growing,
        # shared-prefix conversation to the same cache entry.
        self._prompt_cache_key = uuid.uuid4().hex

    def _reasoning_payload_extra(self) -> dict[str, Any]:
        """Extra payload fields for deep reasoning beyond the flat reasoning_effort field both providers share. DeepSeek's actual v4 API additionally wants an explicit thinking block and stream:false alongside reasoning_effort=high (owner-supplied 2026-08-05); Mistral needs nothing more here."""
        if self._provider == "deepseek":
            return {"thinking": {"type": "enabled"}, "stream": False}
        return {}

    def _effective_max_tokens(self, requested: int | None) -> int:
        """The max_tokens to actually send: `requested` (or MISTRAL_MAX_TOKENS when the caller didn't pass one), raised to DEEPSEEK_MAX_TOKENS as a FLOOR — not a replacement — when this instance is DeepSeek.

        A floor, not just a default-when-None, because several real callers pass
        a small EXPLICIT cap tuned for Mistral's assumption that max_tokens is
        pure answer content (e.g. the LLM quality rubric's max_tokens=800: a
        short JSON scorecard, no reasoning overhead expected). DeepSeek's
        thinking mode spends real, sometimes-large token counts on
        reasoning_content out of that SAME budget — root-caused 2026-08-06:
        the rubric silently failed on every real DeepSeek session with a
        generic fallback score (2/5, boilerplate issues) because 800 tokens
        was entirely consumed by reasoning before any JSON could be written.
        Raising the cap costs nothing by itself (billing is on tokens actually
        used, not the ceiling) so there's no downside to flooring every
        DeepSeek call at the same generous ceiling as the article-write call.
        """
        base = requested if requested is not None else MISTRAL_MAX_TOKENS
        if self._provider == "deepseek":
            return max(base, DEEPSEEK_MAX_TOKENS)
        return base

    def _max_tokens_field_name(self) -> str:
        """Which request field carries the output-token cap. "max_tokens" for every provider this class has served historically (Mistral, DeepSeek) -- confirmed live 2026-08-14 that OpenAI's GPT-5.6 family rejects that field outright ("Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead", 400 unsupported_parameter), so OpenAIProvider overrides this to "max_completion_tokens" instead of hardcoding a payload key here."""
        return "max_tokens"

    def _supports_temperature(self) -> bool:
        """Whether this provider's model accepts an explicit `temperature` value. True for every provider this class has served historically -- confirmed live 2026-08-14 that OpenAI's GPT-5.6 family rejects anything but its own default ("Unsupported value: 'temperature' does not support 0.3 with this model. Only the default (1) value is supported.", 400 unsupported_value), so OpenAIProvider overrides this to False and the field is omitted entirely (letting the API fall back to its own default, which IS accepted)."""
        return True

    def _supports_prompt_cache_key(self) -> bool:
        """Whether to send `prompt_cache_key` on every request. Mistral's own API documents this as the opt-in that pins a growing, shared-prefix conversation to the same server-side cache entry (90% off cached input) -- added 2026-08-14 after finding the agentic writer/research loop was paying full price on every round's already-seen context. Defaults False: DeepSeek, OpenAI, and Kimi already cache automatically with no request-side flag (confirmed 2026-08-14), and sending an unrecognized extra field to a stricter API is a real risk not worth taking for zero benefit -- only MistralProvider overrides this to True."""
        return False

    def usage_totals(self) -> dict[str, int]:
        """Cumulative token usage across every request this instance has made (a compose session's client(s) are created fresh per session, so this is the session total, not a lifetime counter)."""
        return dict(self._usage)

    @property
    def model(self) -> str:
        """The model this instance actually resolved to — read this instead of a config constant when recording which model served a call (a canary-routed instance's real model differs from its purpose's configured default)."""
        return self._model

    @property
    def provider(self) -> str:
        """"mistral" or "deepseek" — which provider this instance actually resolved to."""
        return self._provider

    def _retry_after_network_error(
        self, exc: httpx.RequestError, *, attempt: int, last_attempt: bool
    ) -> None:
        """Log and sleep before retrying a transport-level failure (connection/read timeout, DNS, reset), or raise on the final attempt."""
        if last_attempt:
            raise MistralError(
                f"Mistral request failed after {attempt + 1} attempts: {exc}"
            ) from exc
        wait = min(MISTRAL_BACKOFF_MAX_SECONDS, MISTRAL_BACKOFF_BASE_SECONDS * (2**attempt))
        logger.warning(
            "Mistral network error (attempt %d/%d): %s; backing off %.1fs",
            attempt + 1,
            MISTRAL_MAX_RETRIES + 1,
            exc,
            wait,
        )
        time.sleep(wait)

    def _retry_after_retryable_status(
        self, resp: httpx.Response, *, attempt: int, last_attempt: bool
    ) -> None:
        """Log and sleep (honoring Retry-After) before retrying a 429/5xx, or raise on the final attempt."""
        if last_attempt:
            if resp.status_code == 429:
                raise MistralRateLimitError(
                    f"Mistral API 429 after {attempt + 1} attempts: {resp.text[:300]}"
                )
            raise MistralError(
                f"Mistral API {resp.status_code} after {attempt + 1} attempts: {resp.text[:300]}"
            )
        wait = _retry_after_seconds(resp) or min(
            MISTRAL_BACKOFF_MAX_SECONDS, MISTRAL_BACKOFF_BASE_SECONDS * (2**attempt)
        )
        logger.warning(
            "Mistral %d (attempt %d/%d); backing off %.1fs",
            resp.status_code,
            attempt + 1,
            MISTRAL_MAX_RETRIES + 1,
            wait,
        )
        time.sleep(wait)

    @staticmethod
    def _wants_reasoning_effort_retry(resp: httpx.Response, payload: dict[str, Any]) -> bool:
        """True if a 400 is rejecting the reasoning_effort field itself -- providers phrase this differently (Mistral: "not enabled"; OpenAI/gpt-5.6-luna: "are not supported ... in /v1/chat/completions"), so match on the param name being singled out rather than one exact phrase."""
        if resp.status_code != 400 or "reasoning_effort" not in payload:
            return False
        text = resp.text
        if "reasoning_effort" not in text:
            return False
        return any(
            phrase in text for phrase in ("not enabled", "not supported", "unsupported")
        )

    def _raise_for_error_status(self, resp: httpx.Response) -> None:
        """Raise the appropriate MistralError subtype for a non-retryable error status."""
        if resp.status_code in (401, 402):
            mark_credit_exhausted(self._provider)
            raise MistralCreditError(f"Mistral API {resp.status_code}: {resp.text[:500]}")
        if resp.status_code >= 400:
            raise MistralError(f"Mistral API {resp.status_code}: {resp.text[:500]}")

    def _record_usage(self, data: dict[str, Any]) -> None:
        usage = data.get("usage") or {}
        self._usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        self._usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        self._usage["total_tokens"] += int(usage.get("total_tokens") or 0)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST one chat/completions request through the shared rate-limit gate, retrying on 429 with Retry-After / exponential backoff. Returns the parsed JSON body or raises MistralError."""
        if is_credit_exhausted(self._provider):
            raise MistralCreditError(
                f"{self._provider} credit exhausted (cached — will retry after the monthly reset)"
            )
        url = f"{self._api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        # Retry transient failures, not just 429: server-side 5xx and network /
        # timeout errors are equally transient and were previously fatal on the
        # first try (e.g. a read timeout on the big two-stage revision call).
        for attempt in range(MISTRAL_MAX_RETRIES + 1):
            throttle_llm_call()
            last_attempt = attempt >= MISTRAL_MAX_RETRIES
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                self._retry_after_network_error(exc, attempt=attempt, last_attempt=last_attempt)
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                self._retry_after_retryable_status(resp, attempt=attempt, last_attempt=last_attempt)
                continue
            if self._wants_reasoning_effort_retry(resp, payload):
                self._reasoning_effort_unsupported = True
                payload = {k: v for k, v in payload.items() if k != "reasoning_effort"}
                logger.warning(
                    "Mistral model %s does not support reasoning_effort; retrying without it",
                    self._model,
                )
                continue
            self._raise_for_error_status(resp)
            data = resp.json()
            self._record_usage(data)
            return data
        raise MistralError("Mistral request retry loop exhausted")  # unreachable

    def _log_task_context(self, op: str) -> None:
        """Log which Celery task is driving this Mistral call, so an unexpected burst of API queries can be traced back to the task that caused it."""
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
        """Send a chat-completion request and return the response text."""
        if not self._api_key:
            msg = "MISTRAL_API_KEY is not set"
            raise MistralError(msg)
        self._log_task_context("chat_completion")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            self._max_tokens_field_name(): self._effective_max_tokens(max_tokens),
        }
        if self._supports_temperature():
            payload["temperature"] = temperature
        if self._supports_prompt_cache_key():
            payload["prompt_cache_key"] = self._prompt_cache_key
        if MISTRAL_REASONING_EFFORT and not self._reasoning_effort_unsupported:
            payload["reasoning_effort"] = MISTRAL_REASONING_EFFORT
        payload.update(self._reasoning_payload_extra())
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
        """Send a chat-completion request and parse the response as a JSON object."""
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

    def _merged_convo_with_prior_debug(
        self, messages: list[dict[str, Any]], debug: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        """Prepend any prior chat_with_tools round's transcript stashed on a shared debug dict, so a multi-pass compose (initial research, RESEARCH_FLOOR nudge, digest gap-fill) keeps every round's tool calls in the persisted transcript instead of a later pass silently overwriting it with its own fresh 2-message start."""
        convo = list(messages)
        if debug is not None:
            prior = debug.get("messages")
            if isinstance(prior, list) and prior:
                convo = prior + convo
            # Belt-and-suspenders alongside _ensure_tool_call_ids' per-round
            # backfill: root-caused 2026-08-13 (LumiRogue recompose,
            # ed06b874) that the revision pass's OWN chat_with_tools call —
            # not the research pass that generated `prior` — can still hit
            # "messages[N]: missing field `id`" against Mistral's stricter
            # API despite the per-round backfill existing since 2026-08-13's
            # earlier fix. Static + synthetic testing of the per-round path
            # couldn't reproduce a gap in isolation, so rather than leave the
            # exact mechanism unresolved, re-assert the invariant on the
            # WHOLE merged transcript right here — the one place every
            # later-stage call's outgoing `messages` passes through before a
            # request is ever built, regardless of which round or pass
            # originally produced a given tool_calls entry.
            for idx, m in enumerate(convo):
                tcs = m.get("tool_calls")
                if not tcs:
                    continue
                _ensure_tool_call_ids(tcs)
                # A tool-role message's own tool_call_id is a SEPARATE field
                # on a SEPARATE message, set once at generation time
                # (mistral_client._run_tool_call / mistral_compose's
                # synthetic _debug_tool_turn) and never revisited by the
                # backfill above, which only touches the assistant side.
                # Root-caused 2026-08-15: a synthetic debug-transcript entry
                # (the deterministic grader's bookkeeping turn) built its
                # tool-role pair with no tool_call_id at all; the id backfill
                # above gave the assistant side a fresh id but left the
                # paired tool message pointing at nothing, so a later replay
                # through a stricter provider rejected it ("messages with
                # role 'tool' must have a 'tool_call_id'", GPT-5.6-luna,
                # confirmed live). Re-pairing by position here (the same 1:1
                # ordering _run_tool_call already produces for a real
                # multi-tool-call round) closes this generically, not just
                # for the one call site that happened to trigger it.
                for offset, call in enumerate(tcs):
                    pos = idx + 1 + offset
                    if pos >= len(convo):
                        break
                    tool_msg = convo[pos]
                    if tool_msg.get("role") != "tool":
                        break
                    if not tool_msg.get("tool_call_id"):
                        tool_msg["tool_call_id"] = call.get("id", "")
            debug["messages"] = convo  # mutated in place → full transcript
            debug["model"] = self._model
        return convo

    @staticmethod
    def _seed_seen_calls_from_trace(trace: list[dict[str, Any]] | None) -> set[str]:
        """Cross-pass tool-call dedup cache (2026-07-16), seeded from a shared trace's non-errored calls so an exact repeat in a later pass is nudged instead of silently re-executed (a real RandGallery session once repeated 5 of its 35 calls, ~970k tokens). Errored calls are NOT seeded — retrying a transient failure in a later pass is legitimate."""
        seen_calls: set[str] = set()
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
        return seen_calls

    # Tools that are genuinely optional/low-stakes (a nice-to-have side
    # effect, not research) but where a model can keep finding "new" near-
    # duplicate arguments forever, defeating the exact-signature dedup above
    # entirely — root-caused 2026-08-06: a special-edition session made 33
    # suggest_glossary_term calls (a different term each time, so never an
    # exact repeat) instead of ever transitioning to writing, even after
    # being told directly to stop, piling up enough extra rounds of real
    # context to help exhaust the eventual output budget. Capped per
    # session, not banned outright — a handful of genuinely new terms is
    # legitimate, unbounded is not.
    _CALL_CAPPED_TOOLS: ClassVar[dict[str, int]] = {
        "suggest_glossary_term": 8,
        "suggest_tool": 6,
    }

    @staticmethod
    def _seed_tool_call_counts_from_trace(trace: list[dict[str, Any]] | None) -> dict[str, int]:
        """Per-tool-name call counts already made this session, seeded from the shared trace so a cap holds across every chained stage of a special-edition compose (research, entity-enumeration gap-fill, ...), not just one chat_with_tools invocation."""
        counts: dict[str, int] = {}
        for entry in trace or ():
            name = entry.get("tool")
            if name:
                counts[name] = counts.get(name, 0) + 1
        return counts

    def _run_tool_call(
        self,
        call: dict[str, Any],
        *,
        handlers: dict[str, Any],
        seen_calls: set[str],
        tool_call_counts: dict[str, int],
        require_tool: str | None,
        trace: list[dict[str, Any]] | None,
    ) -> tuple[dict[str, Any], bool]:
        """Execute one model-requested tool call (or refuse/nudge past a cap or exact repeat this session), record it to the trace, and return (tool_result_message, satisfied_require_tool). A StorySpikedError (the writer aborting the article) is recorded to the trace then re-raised uncaught — every other tool failure is caught and fed back as an error result."""
        fn = call.get("function") or {}
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        sig = f"{name}:{json.dumps(args, sort_keys=True)}"
        is_fetch_continuation = name == "fetch_url" and bool(args.get("continue_reading"))
        cap = self._CALL_CAPPED_TOOLS.get(name)
        if cap is not None and tool_call_counts.get(name, 0) >= cap:
            # A varying-argument tool a model can call forever without ever
            # tripping the exact-signature dedup below — refuse outright
            # once it's had a generous allowance, rather than nudge (a nudge
            # here would just be one more low-value round).
            result = {
                "error": (
                    f"{name} has been called {tool_call_counts[name]} times already "
                    "this session — that's enough. Stop calling it and write the "
                    "article now with what you already have."
                )
            }
        elif sig in seen_calls and name != "suggest_tool" and not is_fetch_continuation:
            # Identical call already executed this session — don't re-run
            # the handler or resend the (unchanged) data; nudge to write.
            # fetch_url continuations are exempt: _wrap_fetch_url_scroll
            # tracks a per-URL offset in `context`, so repeated calls with
            # continue_reading=true and the SAME surface arguments (same
            # url/max_chars) are not actually duplicates — each one advances
            # the stateful offset and returns the NEXT window of the page.
            # Root-caused 2026-08-06: the model self-reported (via
            # report_compose_issue) that reading a 62-slide PDF took 6 calls
            # instead of ~3 because it had to keep changing max_chars just to
            # dodge this exact-signature dedup on legitimate continuations.
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
            if cap is not None:
                tool_call_counts[name] = tool_call_counts.get(name, 0) + 1
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
        satisfied_require_tool = name == require_tool
        if trace is not None:
            trace.append({"tool": name, "arguments": args, "result": result})
        message = {
            "role": "tool",
            "name": name,
            "tool_call_id": call.get("id", ""),
            # Structure-preserving cap: trims only the biggest string field
            # (e.g. page text) so links/url/title still survive, unlike the
            # old blind json.dumps(result)[:4000].
            "content": serialize_tool_result(result, MISTRAL_TOOL_RESULT_MAX_CHARS),
        }
        return message, satisfied_require_tool

    def _tool_round_payload(
        self,
        convo: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        response_reserve: int,
        temperature: float,
        round_budget_note: str = "",
    ) -> dict[str, Any]:
        # The note is appended to a COPY for just this one request, never to
        # `convo` itself -- it must never be persisted/echoed back, or it
        # compounds across rounds exactly like the reasoning_content bug
        # _for_conversation_history was built to stop (2026-08-06): round 2
        # would resend round 1's note, round 3 both, etc. A fresh, correct
        # note is cheap to recompute every round; a stale one accumulating
        # in the transcript is not.
        messages = [*convo, {"role": "user", "content": round_budget_note}] if round_budget_note else convo
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            self._max_tokens_field_name(): response_reserve,
            "tools": tools,
            "tool_choice": "auto",
        }
        if self._supports_temperature():
            payload["temperature"] = temperature
        if self._supports_prompt_cache_key():
            payload["prompt_cache_key"] = self._prompt_cache_key
        if MISTRAL_REASONING_EFFORT and not self._reasoning_effort_unsupported:
            payload["reasoning_effort"] = MISTRAL_REASONING_EFFORT
        forced = self._tool_reasoning_effort_override()
        if forced is not None:
            payload["reasoning_effort"] = forced
        payload.update(self._reasoning_payload_extra())
        return payload

    def _tool_reasoning_effort_override(self) -> str | None:
        """Force a specific reasoning_effort value on tool-calling requests specifically, overriding whatever the block above decided (including sending nothing at all). Default None -- no override, today's exact behavior for every provider this class has served historically.

        Confirmed live 2026-08-14: GPT-5.6 rejects function tools + reasoning_effort outright ("Function tools with reasoning_effort are not supported for gpt-5.6-luna in /v1/chat/completions... set reasoning_effort to 'none'", 400) -- and it rejects this REGARDLESS of whether we send the field at all (live metadata already marks it reasoning_effort_unsupported, so the block above never adds it, yet the model still 400s citing reasoning_effort). The model apparently reasons by some non-'none' default internally unless explicitly told otherwise; omitting the field isn't the same as disabling it. OpenAIProvider overrides this to "none".
        """
        return None

    @staticmethod
    def _round_budget_note(round_idx: int, rounds: int) -> str:
        """Live "N of M rounds, K remain" text for show_round_budget=True. On the LAST round, says so explicitly — the model should wrap up with what it has rather than start a new investigation it can't finish."""
        remaining = rounds - round_idx - 1
        if remaining <= 0:
            return (
                f"[research budget: round {round_idx + 1} of {rounds} — this is your LAST "
                "round. Wrap up with what you have rather than starting a new line of "
                "investigation you can't finish.]"
            )
        return (
            f"[research budget: round {round_idx + 1} of {rounds} — {remaining} remain "
            "after this one. Depth is cheap here; if there's more worth verifying, keep "
            "going rather than settling for merely plausible.]"
        )

    @staticmethod
    def _extract_message(data: dict[str, Any]) -> dict[str, Any]:
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise MistralError("unexpected Mistral response shape") from exc

    @staticmethod
    def _handle_no_tool_calls_round(
        convo: list[dict[str, Any]],
        msg: dict[str, Any],
        *,
        last_content: str,
        required_satisfied: bool,
        required_nudged: bool,
        require_tool: str | None,
        round_idx: int,
        debug: dict[str, Any] | None,
    ) -> tuple[bool, bool, str | None]:
        """The model produced no tool calls this round. Returns (should_continue_loop, required_nudged, final_content_or_None).

        Model wants to finish but hasn't called the mandatory tool yet: send it
        back once with an explicit instruction. Only nudges once (via
        required_nudged) so a stubborn model can't loop forever.
        """
        if not required_satisfied and not required_nudged:
            convo.append(_for_conversation_history(msg))
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
            return True, True, None
        if debug is not None:
            debug["rounds"] = round_idx + 1
        return False, required_nudged, last_content

    def _process_tool_calls_round(
        self,
        convo: list[dict[str, Any]],
        msg: dict[str, Any],
        tool_calls: list[dict[str, Any]],
        *,
        handlers: dict[str, Any],
        seen_calls: set[str],
        tool_call_counts: dict[str, int],
        require_tool: str | None,
        trace: list[dict[str, Any]] | None,
        required_satisfied: bool,
        round_idx: int,
        debug: dict[str, Any] | None,
    ) -> tuple[str | None, bool]:
        """Handle a round where the model made tool calls: salvage a bogus-tool-call final article, or execute every real call and append its result. Returns (salvaged_final_or_None, required_satisfied)."""
        # Some models emit their final JSON article as a bogus tool call
        # (function name like ```json or the article itself) instead of
        # message content. Recover it so the article is not lost.
        salvaged = _salvage_final_article(tool_calls, handlers)
        if salvaged is not None:
            if debug is not None:
                debug["rounds"] = round_idx + 1
                debug["salvaged"] = True
            return salvaged, required_satisfied
        convo.append(_for_conversation_history(msg))
        for call in tool_calls:
            tool_message, satisfied = self._run_tool_call(
                call,
                handlers=handlers,
                seen_calls=seen_calls,
                tool_call_counts=tool_call_counts,
                require_tool=require_tool,
                trace=trace,
            )
            if satisfied:
                required_satisfied = True
            convo.append(tool_message)
        return None, required_satisfied

    @staticmethod
    def _fire_on_round(on_round: Callable[[], None] | None) -> None:
        """Best-effort invoke chat_with_tools' per-round callback — a checkpoint failure must never abort the compose loop."""
        if on_round is None:
            return
        try:
            on_round()
        except Exception:
            logger.debug("chat_with_tools on_round callback failed", exc_info=True)

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
        """Agentic loop: let the model call the provided tools, execute them, feed results back, and return the final assistant message content. Tools are real functions the writer invokes on demand (live price, chain stats, platform search, recent articles). Pass ``debug`` to capture the full transcript (it tracks ``convo`` live + records the round count). ``on_round``, if given, fires after every round (whether or not it made tool calls) — callers use this to checkpoint compose_sessions live, since ``trace``/``debug`` are mutated in place round by round but nothing previously re-persisted them until the whole multi-round call returned, leaving the admin Sessions tab showing zero progress for the entire length of a long research pass. Never allowed to abort the loop — a checkpoint failure is swallowed, not raised.

        ``show_round_budget``: inject a live "round N of M, K remain" note
        into each outgoing request (never persisted into the transcript —
        see _tool_round_payload). Off by default; the static one-time
        mention in the research-phase system prompt (RESEARCH BUDGET,
        2026-08-13) already covers most callers cheaply. This is for the
        research pass specifically, where "how much runway do I actually
        have left" is a genuine per-round decision (push an interactive
        flow further, chase one more unverified claim) rather than a
        one-off framing set at the start.
        """
        if not self._api_key:
            raise MistralError("MISTRAL_API_KEY is not set")
        self._log_task_context("chat_with_tools")

        rounds = max_rounds if max_rounds is not None else LLM_MAX_TOOL_ROUNDS
        convo = self._merged_convo_with_prior_debug(messages, debug)
        last_content = ""
        # Guard against runaway loops: the data tools (price, market, chain head)
        # return stable data, but the model otherwise re-calls them dozens of
        # times. Cache (name+args) signatures and refuse to re-run an identical
        # call, nudging the model to write instead.
        seen_calls = self._seed_seen_calls_from_trace(trace)
        tool_call_counts = self._seed_tool_call_counts_from_trace(trace)
        # Enforce a mandatory tool (e.g. review_draft): the model is not allowed
        # to produce its final answer until it has called this tool at least once.
        required_satisfied = require_tool is None
        required_nudged = False
        response_reserve = self._effective_max_tokens(max_tokens)
        # Leave room for the model's reply plus a safety pad below the window.
        # An explicit context_tokens always wins; otherwise prefer this
        # instance's own live-fetched limit (correct for whatever self._model
        # actually is) over the generic hardcoded fallback.
        window = (
            context_tokens
            if context_tokens is not None
            else (self._metadata.get("max_context_length") or MISTRAL_CONTEXT_TOKENS)
        )
        convo_budget = window - response_reserve - MISTRAL_CONTEXT_SAFETY_TOKENS
        for round_idx in range(rounds):
            # Token-aware trim: keep tool results generous, but if many rounds have
            # accumulated and the conversation nears the context window, elide the
            # OLDEST tool results (in place) so the request never overflows.
            if convo_budget > 0:
                fit_messages_to_budget(convo, convo_budget)
            round_budget_note = (
                self._round_budget_note(round_idx, rounds) if show_round_budget else ""
            )
            payload = self._tool_round_payload(
                convo,
                tools=tools,
                response_reserve=response_reserve,
                temperature=temperature,
                round_budget_note=round_budget_note,
            )
            data = self._post(payload)
            msg = self._extract_message(data)
            last_content = _message_text(msg) or last_content
            tool_calls = msg.get("tool_calls") or []
            _ensure_tool_call_ids(tool_calls)
            if not tool_calls:
                should_continue, required_nudged, final = self._handle_no_tool_calls_round(
                    convo,
                    msg,
                    last_content=last_content,
                    required_satisfied=required_satisfied,
                    required_nudged=required_nudged,
                    require_tool=require_tool,
                    round_idx=round_idx,
                    debug=debug,
                )
                if should_continue:
                    self._fire_on_round(on_round)
                    continue
                return final
            salvaged, required_satisfied = self._process_tool_calls_round(
                convo,
                msg,
                tool_calls,
                handlers=handlers,
                seen_calls=seen_calls,
                tool_call_counts=tool_call_counts,
                require_tool=require_tool,
                trace=trace,
                required_satisfied=required_satisfied,
                round_idx=round_idx,
                debug=debug,
            )
            if salvaged is not None:
                return salvaged
            self._fire_on_round(on_round)
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
    """Seconds to wait from a 429 response's Retry-After header (delta-seconds form), or None when absent/unparseable."""
    raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


_PROVIDER_CONFIG: dict[str, tuple[str, int, str]] = {
    # purpose -> (configured default provider, canary %, DeepSeek model for this purpose)
    "writer": (LLM_PROVIDER_WRITER, LLM_PROVIDER_WRITER_CANARY_PCT, DEEPSEEK_MODEL_WRITER),
    "research": (
        LLM_PROVIDER_RESEARCH,
        LLM_PROVIDER_RESEARCH_CANARY_PCT,
        DEEPSEEK_MODEL_RESEARCH,
    ),
    "digest": (LLM_PROVIDER_DIGEST, LLM_PROVIDER_DIGEST_CANARY_PCT, DEEPSEEK_MODEL_DIGEST),
    "translate": (
        LLM_PROVIDER_TRANSLATE,
        LLM_PROVIDER_TRANSLATE_CANARY_PCT,
        DEEPSEEK_MODEL_TRANSLATE,
    ),
    "rubric": (LLM_PROVIDER_RUBRIC, LLM_PROVIDER_RUBRIC_CANARY_PCT, DEEPSEEK_MODEL_RUBRIC),
}


def _select_provider(purpose: str) -> str:
    """Which provider actually serves this call: `purpose`'s configured default, or its canary alternate on a random roll (LLM_PROVIDER_<PURPOSE>_CANARY_PCT). Falls back to mistral if that resolves to deepseek but DEEPSEEK_API_KEY is unset — a canary or override can never hard-fail a compose just because the second provider isn't configured yet."""
    import random

    default_provider, canary_pct, _ = _PROVIDER_CONFIG[purpose]
    provider = default_provider
    if canary_pct > 0 and random.random() * 100 < canary_pct:
        provider = "deepseek" if provider == "mistral" else "mistral"
    if provider == "deepseek" and not DEEPSEEK_API_KEY.strip():
        return "mistral"
    return provider


def _client_for_purpose(
    purpose: str, *, mistral_model: str, timeout: float | None = None
) -> MistralClient:
    """A client for `purpose` ("writer"/"research"/"digest"/"translate"), routed to Mistral or DeepSeek per that purpose's LLM_PROVIDER_<PURPOSE> config (+ canary). `mistral_model` is used when Mistral serves the call — same model as today's behavior when DeepSeek isn't configured, so this is a no-op change until DEEPSEEK_API_KEY is actually set."""
    if _select_provider(purpose) == "deepseek":
        _, _, deepseek_model = _PROVIDER_CONFIG[purpose]
        return MistralClient(
            api_key=DEEPSEEK_API_KEY,
            api_base=DEEPSEEK_API_BASE,
            model=deepseek_model,
            timeout=timeout,
            provider="deepseek",
        )
    return MistralClient(model=mistral_model, timeout=timeout, provider="mistral")


def get_mistral_client(*, model: str | None = None) -> MistralClient:
    """Build a client for the writer tier (or an override model), routed to Mistral or DeepSeek per LLM_PROVIDER_WRITER."""
    return _client_for_purpose("writer", mistral_model=model or MISTRAL_MODEL_WRITER)


def get_mistral_research_client(*, timeout: float | None = None) -> MistralClient:
    """Build a client pinned to the research-tier model, routed to Mistral or DeepSeek per LLM_PROVIDER_RESEARCH, optionally with a non-default per-request timeout (special editions use a longer one -- see MISTRAL_TIMEOUT_SPECIAL_EDITION_MULTIPLIER)."""
    return _client_for_purpose("research", mistral_model=MISTRAL_MODEL_RESEARCH, timeout=timeout)


def get_mistral_digest_client() -> MistralClient:
    """Build a client pinned to the digest-tier model, routed to Mistral or DeepSeek per LLM_PROVIDER_DIGEST."""
    return _client_for_purpose("digest", mistral_model=MISTRAL_MODEL_DIGEST)


def get_mistral_translate_client() -> MistralClient:
    """Build a client pinned to the translate-tier model, routed to Mistral or DeepSeek per LLM_PROVIDER_TRANSLATE. A dedicated factory (rather than get_mistral_client(model=MISTRAL_MODEL_TRANSLATE), the old call pattern) so translate calls can be routed independently of generic writer calls."""
    return _client_for_purpose("translate", mistral_model=MISTRAL_MODEL_TRANSLATE)


def get_mistral_rubric_client(*, timeout: float | None = None) -> MistralClient:
    """Build a client for the LLM quality rubric, routed to Mistral or DeepSeek per LLM_PROVIDER_RUBRIC — independently of LLM_PROVIDER_RESEARCH, even though it shares research's Mistral-side model tier (a judgment task, not generation, doesn't need the writer's Large tier). Split into its own purpose 2026-08-06 so a compose can route its research tool loop to one provider while grading with another."""
    return _client_for_purpose("rubric", mistral_model=MISTRAL_MODEL_RESEARCH, timeout=timeout)
