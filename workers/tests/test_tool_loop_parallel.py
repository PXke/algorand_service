"""Race and ordering guarantees of the tool loop's per-round parallel dispatch.

`_run_tool_calls_parallel` (llm_tool_loop.py) runs one LLM round's tool calls
concurrently. Everything that decides WHETHER a call runs -- the per-session
call cap (`CALL_CAPPED_TOOLS`, real paid spend for search_x) and the
exact-signature dedup cache (`seen_calls`) -- is check-then-mutate on shared
mutable state, which was race-free only because the pre-2026-09-05 loop was a
plain sequential `for` loop.

The property that makes it race-free again is structural, and so is the way
these tests check it: the reservation pass for EVERY call in a round completes
before ANY handler is dispatched. Asserting only on the OUTCOME (three
search_x calls ran, three were refused) does not distinguish the correct
implementation from the naive "check-and-mutate inside the worker thread" one
-- the naive version usually produces the same counts, and only loses the race
under an interleaving no test can schedule on demand. So the reservation tests
here instead run a round with MORE calls than the pool has workers and have
each handler snapshot the shared bookkeeping at entry: with the reservation
pass hoisted out, the very first handler already sees the round's FINAL counts;
with it inlined per worker, the queued calls provably cannot have been reserved
yet, because the only threads that would reserve them are the ones currently
blocked inside a handler. That failure is deterministic, not timing-dependent
(verified by temporarily inlining the reservation into the worker and watching
exactly these tests fail).

The rest pin: a capped or deduped call never reaching a handler at all; `trace`
and the returned entries coming back in ORIGINAL call order even when handlers
finish in exactly the reverse order (forced with chained `threading.Event`s,
not sleeps); the pool bound actually bounding; and the two deliberate
exclusions from the pool -- browser-driving tools (`_SERIALIZED_TOOLS`) and a
story-terminating `abort_article` round.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from app.modules.ai import llm_tool_loop
from app.modules.ai.llm_tool_loop import (
    _SERIALIZED_TOOLS,
    NormalizedToolCall,
    RoundResult,
    ToolLoopAdapter,
    _handle_tool_calls_round,
    _run_tool_calls_parallel,
)
from app.modules.ai.story_spike import StorySpikedError

# Generous: these only ever gate a rendezvous between threads that are
# already running, so a real hang fails fast-ish while a loaded CI box
# (pytest-xdist -n auto) never flakes.
_RENDEZVOUS_TIMEOUT_S = 15.0


def _call(name: str, **args: Any) -> NormalizedToolCall:  # noqa: ANN401 -- arbitrary tool args
    return NormalizedToolCall(id=f"c-{name}-{len(args)}-{id(args)}", name=name, args=args)


def _run(
    calls: list[NormalizedToolCall],
    handlers: dict[str, Any],
    *,
    seen_calls: set[str] | None = None,
    tool_call_counts: dict[str, int] | None = None,
    trace: list[dict[str, Any]] | None = None,
    require_tool: str | None = None,
) -> tuple[list[tuple[NormalizedToolCall, dict[str, Any]]], bool]:
    return _run_tool_calls_parallel(
        calls,
        handlers=handlers,
        seen_calls=set() if seen_calls is None else seen_calls,
        tool_call_counts={} if tool_call_counts is None else tool_call_counts,
        require_tool=require_tool,
        trace=trace,
    )


class _Snapshotter:
    """A fake handler that records the loop's shared bookkeeping at the instant it STARTS.

    Used with a round that has more calls than the pool has workers, this is
    the deterministic race detector. With the reservation pass hoisted out of
    the pool, the very first handler to start already sees the round's final
    `tool_call_counts`/`seen_calls`. With it inlined per worker, it provably
    cannot: the only threads that would reserve the still-queued calls are the
    ones already inside a handler, so the first snapshot can name at most
    `workers` reservations. No sleeping or barriers, so no timing luck either
    way.

    An assertion inside a handler would be swallowed by `_invoke_handler`'s
    broad except, so observations are recorded and asserted in the main thread.
    """

    def __init__(self, seen_calls: set[str], counts: dict[str, int]) -> None:
        self._seen_calls = seen_calls
        self._counts = counts
        self._lock = threading.Lock()
        self.invocations: list[dict[str, Any]] = []
        self.snapshots: list[tuple[int, int]] = []

    def handler(self, **args: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        snapshot = (sum(self._counts.values()), len(self._seen_calls))
        with self._lock:
            self.invocations.append(args)
            self.snapshots.append(snapshot)
        return {"ok": True, **args}


class _Rendezvous:
    """A fake handler that blocks until `parties` of its own invocations are running at once, proving the round really did dispatch them concurrently."""

    def __init__(self, parties: int) -> None:
        self._barrier = threading.Barrier(parties)
        self._lock = threading.Lock()
        self.invocations: list[dict[str, Any]] = []

    def handler(self, **args: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        with self._lock:
            self.invocations.append(args)
        # Trips only if every peer is in flight; otherwise the round hangs to
        # the timeout and the assertions below fail rather than pass quietly.
        self._barrier.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        return {"ok": True, **args}


def test_whole_round_is_cap_reserved_before_any_handler_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six distinct-argument search_x calls with a 2-worker pool: the first handler to start already sees all three of the round's cap reservations, and the other three calls are refused without reaching a handler.

    Distinct queries on purpose -- the dedup cache cannot catch these, so the
    per-session call cap (real per-call money) is the only thing between the
    model and six paid API calls. Six calls against two workers is what makes
    the snapshot decisive: reserving inside the worker could not possibly have
    reached calls 2-5 by the time the first handler runs.
    """
    monkeypatch.setattr(llm_tool_loop, "LLM_TOOL_LOOP_MAX_PARALLEL_CALLS", 2)
    cap = llm_tool_loop.CALL_CAPPED_TOOLS["search_x"]
    assert cap == 3
    seen_calls: set[str] = set()
    counts: dict[str, int] = {}
    fake = _Snapshotter(seen_calls, counts)
    calls = [_call("search_x", query=f"q{i}") for i in range(6)]
    trace: list[dict[str, Any]] = []

    entries, _ = _run(
        calls,
        {"search_x": fake.handler},
        seen_calls=seen_calls,
        tool_call_counts=counts,
        trace=trace,
    )

    assert len(fake.invocations) == cap
    assert sorted(a["query"] for a in fake.invocations) == ["q0", "q1", "q2"]
    assert counts["search_x"] == cap
    # The decisive assertion: every handler, including the first to start,
    # already saw the round's FINAL bookkeeping.
    assert fake.snapshots == [(cap, cap)] * cap
    # Calls 3-5 are refusals, in their original positions, never executed.
    assert [e[1].get("ok") for e in entries] == [True, True, True, None, None, None]
    for _, result in entries[cap:]:
        assert "has been called 3 times already this session" in result["error"]
    assert [t["result"].get("ok") for t in trace] == [True, True, True, None, None, None]


def test_whole_round_is_dedup_reserved_before_any_handler_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three distinct signatures, each repeated once, against a 2-worker pool: the first handler to start already sees all three signatures in `seen_calls`, and the three repeats get the dedup nudge without reaching a handler."""
    monkeypatch.setattr(llm_tool_loop, "LLM_TOOL_LOOP_MAX_PARALLEL_CALLS", 2)
    seen_calls: set[str] = set()
    counts: dict[str, int] = {}
    fake = _Snapshotter(seen_calls, counts)
    queries = ["alpha", "beta", "gamma", "alpha", "beta", "gamma"]
    calls = [_call("search_web", query=q) for q in queries]
    trace: list[dict[str, Any]] = []

    entries, _ = _run(
        calls,
        {"search_web": fake.handler},
        seen_calls=seen_calls,
        tool_call_counts=counts,
        trace=trace,
    )

    assert len(fake.invocations) == 3
    assert sorted(a["query"] for a in fake.invocations) == ["alpha", "beta", "gamma"]
    # search_web is uncapped, so only the dedup half of the snapshot moves.
    assert fake.snapshots == [(0, 3)] * 3
    assert [e[1].get("ok") for e in entries] == [True, True, True, None, None, None]
    for _, result in entries[3:]:
        assert "You already called this tool with these exact arguments" in result["note"]
    assert [t["arguments"]["query"] for t in trace] == queries


def test_call_cap_holds_while_every_permitted_call_runs_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap's outcome under genuine concurrency: with all three permitted search_x handlers provably in flight together (a 3-party barrier), the round still runs exactly three and refuses the rest in place."""
    monkeypatch.setattr(llm_tool_loop, "LLM_TOOL_LOOP_MAX_PARALLEL_CALLS", 3)
    cap = llm_tool_loop.CALL_CAPPED_TOOLS["search_x"]
    fake = _Rendezvous(cap)
    counts: dict[str, int] = {}
    calls = [_call("search_x", query=f"q{i}") for i in range(6)]

    entries, _ = _run(calls, {"search_x": fake.handler}, tool_call_counts=counts)

    assert len(fake.invocations) == cap  # barrier tripped: all three were live
    assert counts["search_x"] == cap
    assert [e[1].get("ok") for e in entries] == [True, True, True, None, None, None]


def test_trace_keeps_call_order_when_handlers_finish_in_reverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third call's handler finishes first and the first call's last, yet `trace` and the returned entries stay in call order.

    Completion order is forced with chained Events rather than sleeps, so this
    is deterministic: `third` returns, which releases `second`, which releases
    `first`. Anything that recorded results as they completed (an
    `as_completed` loop appending straight to `trace`) reverses the order and
    fails here.
    """
    monkeypatch.setattr(llm_tool_loop, "LLM_TOOL_LOOP_MAX_PARALLEL_CALLS", 3)
    third_done = threading.Event()
    second_done = threading.Event()
    completions: list[str] = []
    lock = threading.Lock()

    def _record(name: str) -> dict[str, Any]:
        with lock:
            completions.append(name)
        return {"tool": name}

    def _first(**_: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        assert second_done.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        return _record("first")

    def _second(**_: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        assert third_done.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        out = _record("second")
        second_done.set()
        return out

    def _third(**_: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        out = _record("third")
        third_done.set()
        return out

    calls = [_call("first"), _call("second"), _call("third")]
    trace: list[dict[str, Any]] = []
    entries, _ = _run(
        calls,
        {"first": _first, "second": _second, "third": _third},
        trace=trace,
    )

    assert completions == ["third", "second", "first"]  # really finished backwards
    assert [c.name for c, _ in entries] == ["first", "second", "third"]
    assert [t["tool"] for t in trace] == ["first", "second", "third"]
    assert [t["result"]["tool"] for t in trace] == ["first", "second", "third"]


def test_mixed_round_keeps_order_across_refused_executed_and_malformed_calls() -> None:
    """A round mixing a malformed call, a capped refusal, a dedup nudge and two real handlers records all five in call order, with the malformed one's raw text as its trace arguments."""
    seen_calls = {'search_web:{"query": "old"}'}
    counts = {"search_x": 3}
    calls = [
        NormalizedToolCall(id="1", name="search_web", args=None, raw_args="{bad json"),
        _call("search_x", query="fresh"),
        _call("search_web", query="old"),
        _call("search_web", query="new"),
        _call("get_defi_tvl", protocol="folks"),
    ]
    handlers = {
        "search_web": lambda **a: {"ran": "search_web", **a},
        "get_defi_tvl": lambda **a: {"ran": "get_defi_tvl", **a},
        "search_x": lambda **a: {"ran": "search_x", **a},
    }
    trace: list[dict[str, Any]] = []

    entries, _ = _run(calls, handlers, seen_calls=seen_calls, tool_call_counts=counts, trace=trace)

    assert [c.name for c, _ in entries] == [
        "search_web",
        "search_x",
        "search_web",
        "search_web",
        "get_defi_tvl",
    ]
    assert entries[0][1]["error"] == "malformed tool arguments"
    assert "has been called 3 times" in entries[1][1]["error"]
    assert "already called this tool" in entries[2][1]["note"]
    assert entries[3][1]["ran"] == "search_web"
    assert entries[4][1]["ran"] == "get_defi_tvl"
    assert trace[0]["arguments"] == "{bad json"  # raw text, never a parsed dict
    assert [t["tool"] for t in trace] == [c.name for c in calls]
    # A malformed call never reserves its signature, and a capped one never
    # bumps the count past its cap.
    assert counts["search_x"] == 3


def test_require_tool_is_satisfied_only_by_a_call_that_actually_ran() -> None:
    """A round where the required tool is both executed once and deduped once reports satisfied; a round where it is only deduped does not."""
    seen_calls: set[str] = set()
    entries, satisfied = _run(
        [_call("review_draft", note="a"), _call("review_draft", note="a")],
        {"review_draft": lambda **a: {"ok": True, **a}},
        seen_calls=seen_calls,
        require_tool="review_draft",
    )
    assert satisfied is True
    assert entries[1][1]["note"].startswith("You already called this tool")

    _, satisfied_again = _run(
        [_call("review_draft", note="a")],
        {"review_draft": lambda **a: {"ok": True, **a}},
        seen_calls=seen_calls,  # already seeded by the round above
        require_tool="review_draft",
    )
    assert satisfied_again is False


@pytest.mark.parametrize(
    "serialized_tool",
    ["fetch_url", "play_interactive", "extract_pdf_from_page"],
)
def test_browser_driving_tools_run_one_at_a_time_in_the_calling_thread(
    serialized_tool: str,
) -> None:
    """Tools that drive a real Chromium never overlap with each other and never run on a pool thread; the plain HTTP tools in the same round still do.

    fetch_url/play_interactive share this compose's single PlaywrightSession
    (and its per-compose offset/step state); extract_pdf_from_page launches its
    own throwaway Chromium. Either way two at once is wrong, so both kinds sit
    in `_SERIALIZED_TOOLS`.
    """
    assert serialized_tool in _SERIALIZED_TOOLS
    main_thread = threading.current_thread().name
    lock = threading.Lock()
    live_serialized = 0
    max_live_serialized = 0
    serial_threads: set[str] = set()
    parallel_threads: set[str] = set()
    release = threading.Event()

    def _serialized(**_: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        nonlocal live_serialized, max_live_serialized
        with lock:
            live_serialized += 1
            max_live_serialized = max(max_live_serialized, live_serialized)
            serial_threads.add(threading.current_thread().name)
        # Long enough for a genuinely-parallel peer to be caught overlapping,
        # but released as soon as the pooled call has actually started.
        release.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        with lock:
            live_serialized -= 1
        return {"ok": True}

    def _pooled(**_: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        with lock:
            parallel_threads.add(threading.current_thread().name)
        release.set()
        return {"ok": True}

    calls = [
        _call(serialized_tool, url="https://example.test/a"),
        _call("search_web", query="q"),
        _call(serialized_tool, url="https://example.test/b"),
    ]
    trace: list[dict[str, Any]] = []
    entries, _ = _run(calls, {serialized_tool: _serialized, "search_web": _pooled}, trace=trace)

    assert max_live_serialized == 1  # the two browser calls never overlapped
    assert serial_threads == {main_thread}
    assert parallel_threads
    assert main_thread not in parallel_threads
    assert [t["tool"] for t in trace] == [serialized_tool, "search_web", serialized_tool]
    assert [c.name for c, _ in entries] == [serialized_tool, "search_web", serialized_tool]


class _FakeAdapter(ToolLoopAdapter):
    """Records only what `_handle_tool_calls_round` asks of an adapter."""

    def __init__(self) -> None:
        self.assistant_turns: list[RoundResult] = []
        self.tool_result_batches: list[list[tuple[NormalizedToolCall, dict[str, Any]]]] = []

    def append_assistant_turn(self, round_result: RoundResult) -> None:
        self.assistant_turns.append(round_result)

    def append_tool_results(self, entries: list[tuple[NormalizedToolCall, dict[str, Any]]]) -> None:
        self.tool_result_batches.append(entries)


def test_abort_article_round_stays_sequential_and_never_runs_later_calls() -> None:
    """A round containing abort_article runs through the sequential path: the call before it executes and is traced, the spike is traced, and every call listed after it is never attempted."""
    ran: list[str] = []

    def _ok(**_: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        ran.append("search_web")
        return {"ok": True}

    def _abort(**_: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        raise StorySpikedError("not actually about Algorand", "not_newsworthy")

    def _after(**_: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        ran.append("suggest_glossary_term")  # pragma: no cover -- must never run
        return {"ok": True}

    result = RoundResult(
        text="",
        tool_calls=[
            _call("search_web", query="q"),
            _call("abort_article", reason="off topic"),
            _call("suggest_glossary_term", term="t"),
        ],
    )
    trace: list[dict[str, Any]] = []
    with pytest.raises(StorySpikedError):
        _handle_tool_calls_round(
            _FakeAdapter(),
            result,
            handlers={
                "search_web": _ok,
                "abort_article": _abort,
                "suggest_glossary_term": _after,
            },
            seen_calls=set(),
            tool_call_counts={},
            require_tool=None,
            trace=trace,
            required_satisfied=True,
        )

    assert ran == ["search_web"]  # the post-abort call was never attempted
    assert [t["tool"] for t in trace] == ["search_web", "abort_article"]
    assert trace[1]["result"] == {
        "spiked": True,
        "category": "not_newsworthy",
        "reason": "not actually about Algorand",
    }


def test_a_spike_from_an_unlisted_tool_still_traces_in_call_order() -> None:
    """Safety net: if a tool that is NOT in `_STORY_TERMINATING_TOOLS` ever raises StorySpikedError, the parallel path still records the round's earlier calls plus the spike, in call order, and re-raises -- it does not lose the whole round's trace to an exception crossing a worker thread."""

    def _spike(**_: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        raise StorySpikedError("nothing here", "dead_project")

    calls = [_call("search_web", query="q"), _call("confirm_alert_topic", topic="t")]
    trace: list[dict[str, Any]] = []
    with pytest.raises(StorySpikedError):
        _run(
            calls,
            {"search_web": lambda **a: {"ok": True, **a}, "confirm_alert_topic": _spike},
            trace=trace,
        )

    assert [t["tool"] for t in trace] == ["search_web", "confirm_alert_topic"]
    assert trace[1]["result"] == {
        "spiked": True,
        "category": "dead_project",
        "reason": "nothing here",
    }


def test_a_failing_handler_never_aborts_its_peers_in_the_same_round() -> None:
    """One handler raising an ordinary exception becomes that call's error result; every other call in the round still runs and is recorded in place."""

    def _boom(**_: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        msg = "upstream 503"
        raise RuntimeError(msg)

    calls = [_call("a"), _call("b"), _call("c")]
    trace: list[dict[str, Any]] = []
    entries, _ = _run(
        calls,
        {"a": lambda **_: {"ok": "a"}, "b": _boom, "c": lambda **_: {"ok": "c"}},
        trace=trace,
    )

    assert [e[1].get("ok") for e in entries] == ["a", None, "c"]
    assert entries[1][1]["error"] == "upstream 503"
    assert [t["tool"] for t in trace] == ["a", "b", "c"]


def test_parallel_dispatch_is_bounded_by_the_configured_worker_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A round with far more calls than the cap never runs more than `LLM_TOOL_LOOP_MAX_PARALLEL_CALLS` handlers at once -- one thread per call in a large round would be a real resource problem."""
    monkeypatch.setattr(llm_tool_loop, "LLM_TOOL_LOOP_MAX_PARALLEL_CALLS", 2)
    lock = threading.Lock()
    live = 0
    max_live = 0
    started = threading.Semaphore(0)

    def _handler(**_: Any) -> dict[str, Any]:  # noqa: ANN401 -- arbitrary tool args
        nonlocal live, max_live
        with lock:
            live += 1
            max_live = max(max_live, live)
        started.release()
        # Hold long enough that a third worker, if one existed, would overlap.
        threading.Event().wait(0.02)
        with lock:
            live -= 1
        return {"ok": True}

    calls = [_call("search_web", query=f"q{i}") for i in range(8)]
    entries, _ = _run(calls, {"search_web": _handler})

    assert len(entries) == 8
    assert max_live == 2
