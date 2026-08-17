"""Abstract interface every LLM provider backing the writer/research loop must implement.

Mirrors MistralClient's existing public surface exactly (chat_completion,
chat_json_object, chat_with_tools, usage_totals, .model, .provider) so
mistral_compose.py's orchestration can depend on this interface alone rather
than a concrete client -- any provider (Mistral, DeepSeek, OpenAI, Kimi, GLM,
Gemini, ...) that implements it is a legal, interchangeable research/writer
client. Loaded by name through llm_registry.get_provider(), not constructed
directly by callers that just want "whichever provider this purpose is
configured for".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class LLMError(Exception):
    """Raised on an LLM provider API failure (network, rate limit, malformed response)."""


class LLMCreditError(LLMError):
    """Raised on a provider's own equivalent of 401/402 -- the key is dead or credit is exhausted, no retry will help."""


class LLMProvider(ABC):
    """One provider's chat-completions connector, scoped to exactly what the compose pipeline needs: plain completions, JSON-object completions, and an agentic tool-calling loop, plus per-instance usage accounting."""

    @abstractmethod
    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.3,
    ) -> str:
        """A single plain-text completion, no tools."""

    @abstractmethod
    def chat_json_object(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """A single completion the provider is asked to return as a JSON object, parsed before returning."""

    @abstractmethod
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
        """The agentic tool-calling loop: let the model call the provided tools, execute them, feed results back, and return the final assistant message content."""

    @abstractmethod
    def usage_totals(self) -> dict[str, int]:
        """Cumulative {prompt_tokens, completion_tokens, total_tokens, cached_tokens} across every request this instance has made. cached_tokens is 0 for a provider/response that doesn't report prompt-cache hits (confirmed populated for DeepSeek as of 2026-08-17; other providers carry the key for interface safety but don't populate it yet)."""

    @property
    @abstractmethod
    def model(self) -> str:
        """The model this instance actually resolved to."""

    @property
    @abstractmethod
    def provider(self) -> str:
        """This instance's provider label (e.g. "mistral", "deepseek", "openai", "kimi", "glm", "gemini")."""
