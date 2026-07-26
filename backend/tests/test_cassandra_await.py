"""The ResponseFuture -> asyncio bridge in app.core.cassandra.

Sync tests driving `asyncio.run` rather than `async def` tests: the repo has no
async-test plugin configured, and needing one for six tests is not worth a new
dependency.

The fakes mirror the driver's *documented* callback semantics rather than a
convenient subset, because each of those behaviours is a trap the bridge exists
to handle: callbacks fire on a foreign thread, they fire synchronously when the
result already landed, and exceptions raised inside them are swallowed (so a
mis-set future hangs instead of erroring).
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.core import cassandra as cass
from app.core.cassandra import await_response_future


class _FakeResponseFuture:
    """Mimics cassandra.cluster.ResponseFuture's callback contract.

    `delay` > 0 completes from a separate thread (the driver's I/O thread);
    `delay` == 0 completes before add_callbacks is called, which the driver
    answers by invoking the callback inline on the caller's thread.
    """

    def __init__(
        self, result: object = None, exc: BaseException | None = None, delay: float = 0.01
    ) -> None:
        self._result = result
        self._exc = exc
        self._done = delay == 0
        self._callback = None
        self._errback = None
        self.callback_thread: str | None = None
        if delay > 0:
            threading.Timer(delay, self._complete).start()

    def _complete(self) -> None:
        self._done = True
        self.callback_thread = threading.current_thread().name
        if self._exc is not None:
            if self._errback:
                self._errback(self._exc)
        elif self._callback:
            self._callback(self._result)

    def add_callbacks(self, callback, errback) -> None:  # noqa: ANN001
        self._callback = callback
        self._errback = errback
        if self._done:  # already landed -> driver fires inline, before returning
            self.callback_thread = threading.current_thread().name
            if self._exc is not None:
                errback(self._exc)
            else:
                callback(self._result)


def test_resolves_with_rows_from_another_thread() -> None:
    """Delivers the driver's rows to an awaiting coroutine."""

    async def go() -> object:
        return await await_response_future(_FakeResponseFuture(result=[{"id": 1}], delay=0.01))

    assert asyncio.run(go()) == [{"id": 1}]


def test_resolves_when_result_already_arrived() -> None:
    """Handles the driver firing the callback inline, before add_callbacks returns."""
    fut = _FakeResponseFuture(result=["r"], delay=0)

    async def go() -> object:
        return await await_response_future(fut)

    assert asyncio.run(go()) == ["r"]
    # Proves the inline path was exercised, not the timer path.
    assert fut.callback_thread == threading.main_thread().name


def test_propagates_query_error() -> None:
    """Raises the driver's exception in the awaiting coroutine."""

    async def go() -> object:
        return await await_response_future(
            _FakeResponseFuture(exc=ValueError("no host available"), delay=0.01)
        )

    with pytest.raises(ValueError, match="no host available"):
        asyncio.run(go())


def test_does_not_block_the_event_loop() -> None:
    """The whole point: other tasks keep running while a query is outstanding.

    A blocking `.result()` would let the ticker advance at most once; yielding
    lets it spin freely for the duration of the query.
    """
    ticks = 0

    async def go() -> None:
        nonlocal ticks

        async def ticker() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.001)

        spinner = asyncio.create_task(ticker())
        await await_response_future(_FakeResponseFuture(result=[], delay=0.05))
        spinner.cancel()

    asyncio.run(go())
    assert ticks > 5, f"loop looks blocked; ticker only advanced {ticks} times"


def test_independent_tasks_do_not_serialize() -> None:
    """8 concurrent tasks each issuing a query finish in ~one query's time.

    Each task must SUBMIT its own query inside the coroutine. Submitting all 8
    up front and then collecting would pass even against a blocking `.result()`
    -- all 8 queries are already in flight by then, so the blocking version
    overlaps too (that is the separate `execute_parallel` fan-out win, not this).
    Creating the future inside the task means a stalled loop cannot reach the
    later submissions at all, which is the property under test.
    """

    async def one(i: int) -> object:
        return await await_response_future(_FakeResponseFuture(result=[i], delay=0.05))

    async def go() -> float:
        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.gather(*(one(i) for i in range(8)))
        return loop.time() - started

    elapsed = asyncio.run(go())
    assert elapsed < 0.2, f"8 concurrent queries took {elapsed:.3f}s -- they serialized"


def test_execute_await_returns_a_resultset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wraps rows in a real ResultSet, so call sites written against execute() work unchanged."""
    fut = _FakeResponseFuture(result=[("a", 1)], delay=0.01)
    # ResultSet reads these off the future when it builds itself.
    fut._col_names = ["name", "n"]
    fut._col_types = [str, int]
    # Single page. Iteration consults this -- and a True here is precisely the
    # case execute_await's docstring warns about, since the next page would be
    # fetched with a blocking .result() back on the event loop.
    fut.has_more_pages = False

    class _FakeSession:
        def execute_async(self, statement, params=None):  # noqa: ANN001, ANN202, ARG002
            return fut

    monkeypatch.setattr(cass, "get_cassandra_session", lambda: _FakeSession())

    result = asyncio.run(cass.execute_await("SELECT name, n FROM t WHERE k = ?", ("k",)))
    assert list(result) == [("a", 1)]
    assert result.column_names == ["name", "n"]


def test_late_completion_after_cancel_is_dropped() -> None:
    """A result arriving after cancellation is dropped, not turned into an error.

    The driver ignores exceptions raised inside callbacks, so an unguarded
    set_result here would hang a future rather than surface anything.
    """

    async def go() -> bool:
        awaitable = await_response_future(_FakeResponseFuture(result=["late"], delay=0.02))
        awaitable.cancel()
        await asyncio.sleep(0.05)  # let the timer thread fire into a cancelled future
        return awaitable.cancelled()

    assert asyncio.run(go()) is True
