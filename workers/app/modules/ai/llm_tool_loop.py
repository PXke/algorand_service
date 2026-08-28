"""Shared agentic tool-calling loop, factored out of the three `chat_with_tools` implementations (llm_openai_compatible.py, llm_anthropic_provider.py, llm_gemini_provider.py).

All three providers' `chat_with_tools` do the SAME round-by-round bookkeeping
around a genuinely different request/response wire format each: round-budget
note injection, the cross-call seen-calls dedup cache, the per-tool call-count
cap, require_tool enforcement/nudging, exhaustion + finalize_on_exhaustion,
on_round checkpointing, and trace/debug recording. Before this module existed
that control flow was hand-copied three times and had already drifted (only
OpenAICompatibleProvider had the dedup cache, the call cap, and a full
transcript in `debug["messages"]` -- Anthropic/Gemini recorded neither).

`run_tool_loop` owns that control flow once. Each provider still owns 100% of
its own request shaping, response parsing, and message-format translation --
Anthropic's Messages API (top-level `system`, tool_use/tool_result content
blocks) and Gemini's native generateContent API (contents/parts, functionCall,
role="model") are genuinely not OpenAI-compatible, so a provider builds a
small `ToolLoopAdapter` per `chat_with_tools` call that knows how to send one
round's request and how to fold this round's assistant turn / tool results /
require_tool nudge back into ITS OWN conversation state; the loop driving
those calls doesn't need to know or care what shape that state is in.

Context-window trimming (`fit_messages_to_budget`) and the bogus-tool-call
salvage path stay OpenAI-specific (see llm_openai_compatible.py) -- they're
about that one wire format's own failure modes, not loop control flow, so
they live in the adapter, not here. `try_salvage` is the only optional hook;
every other provider's adapter simply doesn't override it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config import LLM_MAX_TOOL_ROUNDS
from app.modules.ai.story_spike import StorySpikedError

logger = logging.getLogger(__name__)


@dataclass
class NormalizedToolCall:
    """One provider-agnostic view of a single tool call the model asked to run this round.

    `args` is None only for a call whose arguments failed to parse (currently
    only possible for OpenAI-compatible providers, whose `function.arguments`
    is a JSON *string* the model can send malformed; Anthropic's `input` and
    Gemini's `args` arrive already-parsed from the response body, so their
    adapters never produce this). `raw_args` is the original unparsed text,
    used only to report a malformed call without ever running its handler.
    """

    id: str
    name: str
    args: dict[str, Any] | None
    raw_args: str = ""


@dataclass
class RoundResult:
    """One round's response, normalized enough for the shared loop to drive on.

    `raw` is opaque to the loop -- whatever provider-native payload the
    adapter needs later to echo this round's assistant turn back into its own
    conversation state (the full response message dict for OpenAI, the
    content-block list for Anthropic/Gemini).
    """

    text: str
    tool_calls: list[NormalizedToolCall] = field(default_factory=list)
    raw: Any = None


# Tools that are genuinely optional/low-stakes (a nice-to-have side effect,
# not research) but where a model can keep finding "new" near-duplicate
# arguments forever, defeating the exact-signature dedup cache entirely --
# root-caused 2026-08-06 (a special-edition session made 33 suggest_glossary_
# term calls, a different term each time, instead of ever transitioning to
# writing) and reworked 2026-08-25 for search_x (now a free cached lookup,
# kept capped anyway as a runaway-tool-loop guard, not a cost control -- see
# X_SEARCH_ENABLED's own comment in core/config.py). Provider-agnostic: every
# provider's writer session shares the same writer_tools.py registry and is
# equally vulnerable to this failure class, so the cap applies uniformly
# regardless of which provider is running the loop.
CALL_CAPPED_TOOLS: dict[str, int] = {
    "suggest_glossary_term": 8,
    "suggest_tool": 6,
    "search_x": 3,
}


def round_budget_note(round_idx: int, rounds: int) -> str:
    """Live "N of M rounds, K remain" text for show_round_budget=True. On the LAST round, says so explicitly -- the model should wrap up with what it has rather than start a new investigation it can't finish."""
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


def seed_seen_calls_from_trace(trace: list[dict[str, Any]] | None) -> set[str]:
    """Cross-pass tool-call dedup cache (2026-07-16), seeded from a shared trace's non-errored calls so an exact repeat in a later pass is nudged instead of silently re-executed (a real RandGallery session once repeated 5 of its 35 calls, ~970k tokens). Errored calls are NOT seeded -- retrying a transient failure in a later pass is legitimate."""
    seen_calls: set[str] = set()
    for entry in trace or ():
        result = entry.get("result")
        if isinstance(result, dict) and result.get("error"):
            continue
        try:
            seen_calls.add(
                f"{entry.get('tool')}:{json.dumps(entry.get('arguments') or {}, sort_keys=True)}"
            )
        except (TypeError, ValueError):
            continue
    return seen_calls


def seed_tool_call_counts_from_trace(trace: list[dict[str, Any]] | None) -> dict[str, int]:
    """Per-tool-name call counts already made this session, seeded from the shared trace so a cap holds across every chained stage of a multi-pass compose (research, entity-enumeration gap-fill, ...), not just one chat_with_tools invocation."""
    counts: dict[str, int] = {}
    for entry in trace or ():
        name = entry.get("tool")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _capped_refusal(name: str, count: int) -> dict[str, Any]:
    """Refuse a varying-argument tool that's hit its per-session call cap -- a model can keep finding "new" near-duplicate arguments forever, defeating the exact-signature dedup below entirely, so this refuses outright once it's had a generous allowance rather than nudge (a nudge here would just be one more low-value round)."""
    return {
        "error": (
            f"{name} has been called {count} times already this session — "
            "that's enough. Stop calling it and write the article now with "
            "what you already have."
        )
    }


def _is_dedup_nudge_case(sig: str, seen_calls: set[str], name: str, args: dict[str, Any]) -> bool:
    """Whether an exact repeat of `sig` should be nudged instead of re-executed. `suggest_tool` is exempt (llm_compose.py tracks its own per-session count separately) and so is a fetch_url continuation: each continue_reading=true call advances a stateful per-URL offset and returns the NEXT window of the page, so a same-arguments repeat there is not actually a duplicate."""
    if sig not in seen_calls:
        return False
    if name == "suggest_tool":
        return False
    return not (name == "fetch_url" and bool(args.get("continue_reading")))


def _dedup_note(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """The nudge fed back for an identical call already executed this session -- don't re-run the handler or resend the (unchanged) data."""
    note = (
        "You already called this tool with these exact arguments this "
        "session; its data has not changed. Do NOT call it again — use "
        "the result you already have and write the article now."
    )
    if name == "fetch_url" and not args.get("continue_reading"):
        note += (
            " If you meant to read more of a long page, call fetch_url "
            "again with the same url and continue_reading=true."
        )
    return {"note": note}


def _invoke_handler(
    name: str,
    args: dict[str, Any],
    *,
    handlers: dict[str, Any],
    require_tool: str | None,
    trace: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], bool]:
    """Actually run the tool handler and return (result, satisfied_require_tool). A StorySpikedError (the writer aborting the article, e.g. abort_article) is recorded to the trace then re-raised uncaught -- every other tool failure is caught and fed back as an error result."""
    handler = handlers.get(name)
    try:
        result = handler(**args) if handler else {"error": f"unknown tool {name}"}
        # Only a call that actually reached and ran the handler can satisfy
        # a `require_tool` gate -- a capped refusal or a dedup nudge never
        # runs it, so it must never count.
        return result, name == require_tool
    except StorySpikedError as spike:
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
        return {"error": str(exc)}, False


def _execute_tool_call(
    call: NormalizedToolCall,
    *,
    handlers: dict[str, Any],
    seen_calls: set[str],
    tool_call_counts: dict[str, int],
    require_tool: str | None,
    trace: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], bool]:
    """Execute one model-requested tool call (or refuse/nudge past a cap or exact repeat this session), record it to the trace, and return (result, satisfied_require_tool)."""
    if call.args is None:
        malformed: dict[str, Any] = {"error": "malformed tool arguments"}
        if trace is not None:
            trace.append({"tool": call.name, "arguments": call.raw_args, "result": malformed})
        return malformed, False

    name, args = call.name, call.args
    cap = CALL_CAPPED_TOOLS.get(name)
    sig = f"{name}:{json.dumps(args, sort_keys=True)}"

    if cap is not None and tool_call_counts.get(name, 0) >= cap:
        result, satisfied_require_tool = _capped_refusal(name, tool_call_counts[name]), False
    elif _is_dedup_nudge_case(sig, seen_calls, name, args):
        result, satisfied_require_tool = _dedup_note(name, args), False
    else:
        seen_calls.add(sig)
        if cap is not None:
            tool_call_counts[name] = tool_call_counts.get(name, 0) + 1
        result, satisfied_require_tool = _invoke_handler(
            name, args, handlers=handlers, require_tool=require_tool, trace=trace
        )

    if trace is not None:
        trace.append({"tool": name, "arguments": args, "result": result})
    return result, satisfied_require_tool


class ToolLoopAdapter:
    """Provider-specific hooks `run_tool_loop` drives.

    A provider builds one of these (or a subclass) fresh per
    `chat_with_tools` call and hands it to `run_tool_loop`; the loop owns
    everything described in this module's docstring identically for every
    adapter. Only `try_salvage` has a default (no-op) -- every other hook is
    genuinely required, since it's the actual request/response shaping the
    loop has no way to do generically.
    """

    def prepare(self, debug: dict[str, Any] | None) -> None:
        """One-time setup before round 1: wire up debug["messages"]/["model"] and any provider-specific merge/backfill this adapter needs. Default: no-op (an adapter with nothing to prepare, e.g. in a test double, doesn't have to override this)."""

    def send_round(
        self,
        *,
        tools: list[dict[str, Any]],
        temperature: float,
        max_tokens: int | None,
        round_budget_note: str,
    ) -> RoundResult:
        """Send one round's request and return its normalized result."""
        raise NotImplementedError

    def append_assistant_turn(self, round_result: RoundResult) -> None:
        """Echo this round's assistant turn into the adapter's own conversation state -- called before running any of this round's tool calls (or before the require_tool nudge), matching every existing provider's ordering: the assistant's turn is on the record even if a tool call in this same round then raises StorySpikedError."""
        raise NotImplementedError

    def append_tool_results(self, entries: list[tuple[NormalizedToolCall, dict[str, Any]]]) -> None:
        """Fold this round's (call, result) pairs back into the adapter's own conversation state, in its native shape."""
        raise NotImplementedError

    def append_require_tool_nudge(self, require_tool: str) -> None:
        """Append the "you must call X before finishing" nudge message."""
        raise NotImplementedError

    def try_salvage(self, round_result: RoundResult) -> str | None:
        """Recover a final article the model wrongly emitted as a bogus tool call, or None. Default: never salvages -- only OpenAICompatibleProvider's wire format has this failure mode (see llm_openai_compatible._salvage_final_article)."""
        del round_result
        return None

    def finalize(self, *, temperature: float, max_tokens: int | None) -> str:
        """Out of rounds: ask once more, no tools, for a final write-up."""
        raise NotImplementedError


def _handle_no_tool_calls_round(
    adapter: ToolLoopAdapter,
    result: RoundResult,
    *,
    require_tool: str | None,
    required_satisfied: bool,
    required_nudged: bool,
    last_content: str,
) -> tuple[bool, bool, str | None]:
    """The model produced no tool calls this round. Returns (should_continue_loop, required_nudged, final_content_or_None).

    Model wants to finish but hasn't called the mandatory tool yet: send it
    back once with an explicit instruction. Only nudges once (via
    required_nudged) so a stubborn model can't loop forever.
    """
    if not required_satisfied and not required_nudged:
        adapter.append_assistant_turn(result)
        adapter.append_require_tool_nudge(require_tool)  # type: ignore[arg-type]
        return True, True, None
    return False, required_nudged, last_content


def _handle_tool_calls_round(
    adapter: ToolLoopAdapter,
    result: RoundResult,
    *,
    handlers: dict[str, Any],
    seen_calls: set[str],
    tool_call_counts: dict[str, int],
    require_tool: str | None,
    trace: list[dict[str, Any]] | None,
    required_satisfied: bool,
) -> tuple[str | None, bool]:
    """Handle a round where the model made tool calls: salvage a bogus-tool-call final article, or execute every real call and fold the results back in. Returns (salvaged_final_or_None, required_satisfied)."""
    salvaged = adapter.try_salvage(result)
    if salvaged is not None:
        return salvaged, required_satisfied

    adapter.append_assistant_turn(result)
    entries: list[tuple[NormalizedToolCall, dict[str, Any]]] = []
    for call in result.tool_calls:
        tool_result, satisfied = _execute_tool_call(
            call,
            handlers=handlers,
            seen_calls=seen_calls,
            tool_call_counts=tool_call_counts,
            require_tool=require_tool,
            trace=trace,
        )
        if satisfied:
            required_satisfied = True
        entries.append((call, tool_result))
    adapter.append_tool_results(entries)
    return None, required_satisfied


@dataclass
class _LoopState:
    """Mutable state threaded through every round of one `run_tool_loop` call."""

    seen_calls: set[str]
    tool_call_counts: dict[str, int]
    required_satisfied: bool
    required_nudged: bool = False
    last_content: str = ""


def _run_one_round(
    adapter: ToolLoopAdapter,
    state: _LoopState,
    *,
    round_idx: int,
    rounds: int,
    tools: list[dict[str, Any]],
    handlers: dict[str, Any],
    require_tool: str | None,
    trace: list[dict[str, Any]] | None,
    temperature: float,
    max_tokens: int | None,
    show_round_budget: bool,
    debug: dict[str, Any] | None,
    fire_on_round: Callable[[int], None],
) -> str | None:
    """Run one round, mutating `state` in place. Returns the final text if the loop should stop here, or None to keep looping."""
    note = round_budget_note(round_idx, rounds) if show_round_budget else ""
    result = adapter.send_round(
        tools=tools, temperature=temperature, max_tokens=max_tokens, round_budget_note=note
    )
    state.last_content = result.text or state.last_content

    if not result.tool_calls:
        should_continue, state.required_nudged, final = _handle_no_tool_calls_round(
            adapter,
            result,
            require_tool=require_tool,
            required_satisfied=state.required_satisfied,
            required_nudged=state.required_nudged,
            last_content=state.last_content,
        )
        if should_continue:
            fire_on_round(round_idx)
            return None
        if debug is not None:
            debug["rounds"] = round_idx + 1
        return final

    salvaged, state.required_satisfied = _handle_tool_calls_round(
        adapter,
        result,
        handlers=handlers,
        seen_calls=state.seen_calls,
        tool_call_counts=state.tool_call_counts,
        require_tool=require_tool,
        trace=trace,
        required_satisfied=state.required_satisfied,
    )
    if salvaged is not None:
        if debug is not None:
            debug["rounds"] = round_idx + 1
            debug["salvaged"] = True
        return salvaged
    fire_on_round(round_idx)
    return None


def run_tool_loop(
    adapter: ToolLoopAdapter,
    *,
    tools: list[dict[str, Any]],
    handlers: dict[str, Any],
    max_rounds: int | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.6,
    trace: list[dict[str, Any]] | None = None,
    debug: dict[str, Any] | None = None,
    require_tool: str | None = None,
    finalize_on_exhaustion: bool = True,
    on_round: Callable[[], None] | None = None,
    show_round_budget: bool = False,
) -> str:
    """Drive `adapter` through the agentic tool-calling loop and return the final assistant text. See module docstring for what's shared here vs. left to the adapter."""
    rounds = max_rounds if max_rounds is not None else LLM_MAX_TOOL_ROUNDS
    adapter.prepare(debug)
    state = _LoopState(
        seen_calls=seed_seen_calls_from_trace(trace),
        tool_call_counts=seed_tool_call_counts_from_trace(trace),
        required_satisfied=require_tool is None,
    )

    def _fire_on_round(round_idx: int) -> None:
        """Best-effort invoke the caller's per-round callback -- a checkpoint failure must never abort the compose loop. Also updates debug["rounds"] to the round just completed BEFORE firing the callback, so a live checkpoint fired mid-loop reflects genuine progress instead of staying at 0 until the whole call returns."""
        if debug is not None:
            debug["rounds"] = round_idx + 1
        if on_round is None:
            return
        try:
            on_round()
        except Exception:
            logger.debug("chat_with_tools on_round callback failed", exc_info=True)

    for round_idx in range(rounds):
        outcome = _run_one_round(
            adapter,
            state,
            round_idx=round_idx,
            rounds=rounds,
            tools=tools,
            handlers=handlers,
            require_tool=require_tool,
            trace=trace,
            temperature=temperature,
            max_tokens=max_tokens,
            show_round_budget=show_round_budget,
            debug=debug,
            fire_on_round=_fire_on_round,
        )
        if outcome is not None:
            return outcome

    # Out of rounds. Research/gap-fill callers invoke this loop for its tool
    # side-effects (the trace) and DISCARD the return value -- burning a full
    # completion asking for a final write-up on exhaustion was pure waste
    # (confirmed 2026-07-14: a gap-fill pass ran out of rounds and paid for an
    # article nobody read). Those call sites pass finalize_on_exhaustion=False.
    if debug is not None:
        debug["rounds"] = rounds
        debug["exhausted"] = True
    if not finalize_on_exhaustion:
        return state.last_content
    return adapter.finalize(temperature=temperature, max_tokens=max_tokens)
