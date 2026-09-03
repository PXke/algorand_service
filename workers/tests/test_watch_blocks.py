"""single_flight locking for the chain-tail beat (process_new_rounds).

Beat fires every CHAIN_TAIL_POLL_SECONDS (default 60s); a run can process up
to CHAIN_TAIL_MAX_ROUNDS_PER_RUN rounds, each paying for its own algod call,
so a slow run can outrun the beat interval. Without single_flight the next
tick would overlap it: both runs re-process the same rounds (double-firing
publish_from_chain_event for the same on-chain event), and whichever run's
final `client.set` lands last silently rewinds the cursor, undoing the other
run's progress. See CLAUDE.md invariant 5.
"""

from __future__ import annotations

from typing import Never

import pytest
from conftest import FakeRedis

import app.modules.chain_tail.tasks.watch_blocks as wb


def _stub_clean_run(monkeypatch: pytest.MonkeyPatch, *, head: int = 3) -> None:
    """Wire every collaborator process_new_rounds touches to a no-network stub, so a full (non-locked-out) run completes cleanly."""
    monkeypatch.setattr(wb, "chain_crawl_disabled_reason", lambda: None)
    monkeypatch.setattr(wb, "get_conduit_head_round", lambda: head)
    monkeypatch.setattr(wb, "get_algod_head_round", lambda: head)
    monkeypatch.setattr(wb, "clear_registry_cache", lambda: None)
    monkeypatch.setattr(wb, "load_enabled_services", lambda: ())
    monkeypatch.setattr(wb, "list_transactions_for_round", lambda _round_num: [])


def test_process_new_rounds_skips_cleanly_when_lock_already_held(
    monkeypatch: pytest.MonkeyPatch, patch_redis_from_url: FakeRedis
) -> None:
    """A concurrent process_new_rounds invocation must not race the first run -- must return `already_running` without ever entering the round-processing body, touching the cursor, or dispatching publish_from_chain_event."""
    patch_redis_from_url.store[wb.CHAIN_TAIL_LAST_PROCESSED_KEY] = "3"
    # Simulate the lock already held by another worker: pre-seed the lock key
    # so acquire()'s nx=True set fails.
    patch_redis_from_url.store["lock:chain_tail:process_new_rounds"] = "someone-elses-token"

    def _boom() -> Never:
        raise AssertionError("round-processing body must not run while the lock is held")

    monkeypatch.setattr(wb, "chain_crawl_disabled_reason", _boom)

    result = wb.process_new_rounds()

    assert result == {"status": "already_running", "key": "chain_tail:process_new_rounds"}
    # Cursor must be untouched by the skipped run.
    assert patch_redis_from_url.store[wb.CHAIN_TAIL_LAST_PROCESSED_KEY] == "3"


def test_process_new_rounds_lock_ttl_covers_the_hard_task_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lock TTL must be at least the task's soft time limit (CLAUDE.md invariant 5). process_new_rounds has no per-task time-limit override, so it inherits the celery-wide task_time_limit -- pinning the lock to that HARD bound mirrors drain_url_queue/drain_to_compose's precedent so the lock always outlives the run even past a hard SIGKILL."""
    from app.celery_app import celery_app

    seen_ttls: list[int] = []

    def _spy_acquire(_key: str, ttl: int) -> str:
        seen_ttls.append(ttl)
        return "token"

    monkeypatch.setattr("app.core.redis_lock.acquire", _spy_acquire)
    monkeypatch.setattr("app.core.redis_lock.release", lambda _key, _token: None)
    # crawler_chain_disabled short-circuits the body immediately after the
    # lock is acquired -- this test only cares about the ttl bound passed to
    # acquire(), not the round-processing logic itself.
    monkeypatch.setattr(wb, "chain_crawl_disabled_reason", lambda: "crawler_chain_disabled")

    wb.process_new_rounds()

    assert seen_ttls == [celery_app.conf.task_time_limit]


def test_process_new_rounds_fails_open_when_the_lock_check_itself_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis blip on the lock acquisition itself must not stop the chain-tail beat (CLAUDE.md invariant 9: cooldown/lock/budget checks that touch Redis fail OPEN with a log line). single_flight's acquire() already fails open by design (catches the error and returns a token); this pins that the beat actually runs to completion on that path, not just that acquire() itself doesn't raise."""
    import redis

    class _FlakyLockRedis(FakeRedis):
        def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
            if nx:
                raise ConnectionError("redis unreachable")
            return super().set(key, value, nx=nx, ex=ex)

    flaky = _FlakyLockRedis()
    flaky.store[wb.CHAIN_TAIL_LAST_PROCESSED_KEY] = "0"
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: flaky)

    _stub_clean_run(monkeypatch, head=3)

    result = wb.process_new_rounds()

    assert result["status"] == "ok"
    assert result["rounds_processed"] == 3
    assert flaky.store[wb.CHAIN_TAIL_LAST_PROCESSED_KEY] == "3"


def test_process_new_rounds_runs_normally_and_advances_the_cursor_when_unlocked(
    monkeypatch: pytest.MonkeyPatch, patch_redis_from_url: FakeRedis
) -> None:
    """Sanity check for the two lock-path tests above: with no lock held and no Redis error, a normal run still processes rounds and advances the cursor exactly as before this change."""
    patch_redis_from_url.store[wb.CHAIN_TAIL_LAST_PROCESSED_KEY] = "0"
    _stub_clean_run(monkeypatch, head=2)

    result = wb.process_new_rounds()

    assert result["status"] == "ok"
    assert result["rounds_processed"] == 2
    assert result["last_processed"] == 2
    assert patch_redis_from_url.store[wb.CHAIN_TAIL_LAST_PROCESSED_KEY] == "2"
