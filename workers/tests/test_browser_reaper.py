"""Orphaned-Playwright/Chromium process reaper (root-caused 2026-08-26 -- see browser_reaper.py's module docstring for why the app-level try/finally cleanup already in browser_scrape.py can't cover a forceful worker kill)."""

from __future__ import annotations

from unittest.mock import patch

from app.modules.scraper.core.browser_reaper import (
    DEFAULT_MIN_AGE_SECONDS,
    _Proc,
    reap_orphaned_browser_processes,
)

_WORKER_CMD = (
    "/venv/bin/python /venv/bin/celery -A app.celery_app worker "
    "--loglevel=INFO -Q default,scrape,pipeline,chain,security --concurrency=4"
)
_DRIVER_CMD = (
    "/venv/lib/playwright/driver/node /venv/lib/playwright/driver/package/cli.js run-driver"
)
_CHROME_MAIN_CMD = (
    "/home/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell "
    "--headless --no-sandbox --user-data-dir=/tmp/playwright_chromiumdev_profile-abc123"
)
_CHROME_ZYGOTE_CMD = (
    "/home/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell "
    "--type=zygote --no-sandbox --headless"
)


def _procs(*procs: _Proc) -> dict[int, _Proc]:
    return {p.pid: p for p in procs}


def test_live_tree_under_a_worker_is_never_touched() -> None:
    """The exact shape confirmed live on the prod box: worker -> driver/node -> chrome main -> zygote, all still alive -- nothing gets killed."""
    procs = _procs(
        _Proc(pid=100, ppid=1, etimes=99999, cmd=_WORKER_CMD),
        _Proc(pid=200, ppid=100, etimes=30, cmd=_DRIVER_CMD),
        _Proc(pid=201, ppid=200, etimes=29, cmd=_CHROME_MAIN_CMD),
        _Proc(pid=202, ppid=201, etimes=28, cmd=_CHROME_ZYGOTE_CMD),
    )
    with patch(
        "app.modules.scraper.core.browser_reaper._list_processes", return_value=procs
    ), patch("app.modules.scraper.core.browser_reaper.os.kill") as mock_kill:
        result = reap_orphaned_browser_processes(min_age_seconds=0)

    assert result["trees_killed"] == 0
    assert result["pids_killed"] == 0
    mock_kill.assert_not_called()


def test_orphaned_tree_with_no_live_worker_ancestor_is_killed() -> None:
    """Driver reparented to init (ppid=1, its original worker parent is long gone) -- the whole tree (driver + chrome + zygote) gets SIGKILLed."""
    procs = _procs(
        _Proc(pid=200, ppid=1, etimes=600, cmd=_DRIVER_CMD),
        _Proc(pid=201, ppid=200, etimes=599, cmd=_CHROME_MAIN_CMD),
        _Proc(pid=202, ppid=201, etimes=598, cmd=_CHROME_ZYGOTE_CMD),
    )
    with patch(
        "app.modules.scraper.core.browser_reaper._list_processes", return_value=procs
    ), patch("app.modules.scraper.core.browser_reaper.os.kill") as mock_kill:
        result = reap_orphaned_browser_processes(min_age_seconds=DEFAULT_MIN_AGE_SECONDS)

    assert result["trees_killed"] == 1
    assert result["pids_killed"] == 3
    killed_pids = {call.args[0] for call in mock_kill.call_args_list}
    assert killed_pids == {200, 201, 202}


def test_orphaned_chrome_with_dead_driver_is_still_caught() -> None:
    """The driver process itself already exited (e.g. it noticed stdin EOF and cleaned up), but Chromium -- its own separate session -- did not. Chrome's ppid points at a pid that's no longer in the process table at all, so it's its own root."""
    procs = _procs(
        _Proc(pid=201, ppid=200, etimes=600, cmd=_CHROME_MAIN_CMD),  # pid 200 (driver) is gone
        _Proc(pid=202, ppid=201, etimes=599, cmd=_CHROME_ZYGOTE_CMD),
    )
    with patch(
        "app.modules.scraper.core.browser_reaper._list_processes", return_value=procs
    ), patch("app.modules.scraper.core.browser_reaper.os.kill") as mock_kill:
        result = reap_orphaned_browser_processes(min_age_seconds=0)

    assert result["trees_killed"] == 1
    killed_pids = {call.args[0] for call in mock_kill.call_args_list}
    assert killed_pids == {201, 202}


def test_young_orphan_is_skipped_to_avoid_a_reparenting_race() -> None:
    """A tree that LOOKS orphaned but is still well under the age floor is left alone -- it might just be mid-launch, before its parent linkage (or the worker list itself) has settled. It'll be caught on a later sweep if it's still orphaned then."""
    procs = _procs(
        _Proc(pid=200, ppid=1, etimes=5, cmd=_DRIVER_CMD),
        _Proc(pid=201, ppid=200, etimes=4, cmd=_CHROME_MAIN_CMD),
    )
    with patch(
        "app.modules.scraper.core.browser_reaper._list_processes", return_value=procs
    ), patch("app.modules.scraper.core.browser_reaper.os.kill") as mock_kill:
        result = reap_orphaned_browser_processes(min_age_seconds=DEFAULT_MIN_AGE_SECONDS)

    assert result["trees_killed"] == 0
    assert result["skipped_too_young"] == 1
    mock_kill.assert_not_called()


def test_deep_ancestor_chain_still_finds_a_live_worker() -> None:
    """The worker doesn't have to be the IMMEDIATE parent -- any live ancestor within the hop limit counts (matches the real shape: worker main -> forked worker child -> driver/node -> chrome)."""
    procs = _procs(
        _Proc(pid=1, ppid=0, etimes=99999, cmd="/sbin/init"),
        _Proc(pid=90, ppid=1, etimes=99999, cmd=_WORKER_CMD + " (main)"),
        _Proc(pid=100, ppid=90, etimes=99999, cmd=_WORKER_CMD),
        _Proc(pid=200, ppid=100, etimes=30, cmd=_DRIVER_CMD),
        _Proc(pid=201, ppid=200, etimes=29, cmd=_CHROME_MAIN_CMD),
    )
    with patch(
        "app.modules.scraper.core.browser_reaper._list_processes", return_value=procs
    ), patch("app.modules.scraper.core.browser_reaper.os.kill") as mock_kill:
        result = reap_orphaned_browser_processes(min_age_seconds=0)

    assert result["trees_killed"] == 0
    mock_kill.assert_not_called()


def test_dry_run_reports_without_killing() -> None:
    """dry_run=True still reports what WOULD be killed, for a manual/offline check, but calls os.kill zero times."""
    procs = _procs(
        _Proc(pid=200, ppid=1, etimes=600, cmd=_DRIVER_CMD),
        _Proc(pid=201, ppid=200, etimes=599, cmd=_CHROME_MAIN_CMD),
    )
    with patch(
        "app.modules.scraper.core.browser_reaper._list_processes", return_value=procs
    ), patch("app.modules.scraper.core.browser_reaper.os.kill") as mock_kill:
        result = reap_orphaned_browser_processes(min_age_seconds=0, dry_run=True)

    assert result["trees_killed"] == 1
    mock_kill.assert_not_called()


def test_process_listing_failure_never_raises() -> None:
    """A failed `ps` call (missing binary, permissions) must not blow up the beat -- just report nothing killed and retry next sweep."""
    with patch(
        "app.modules.scraper.core.browser_reaper._list_processes",
        side_effect=RuntimeError("ps not found"),
    ):
        result = reap_orphaned_browser_processes()

    assert result["trees_killed"] == 0
    assert result.get("error") is True


def test_unrelated_processes_are_never_considered() -> None:
    """Only chrome-headless-shell / playwright driver processes are ever candidates -- an ordinary orphaned process (e.g. a stray shell) must never be touched by this sweep."""
    procs = _procs(
        _Proc(pid=500, ppid=1, etimes=99999, cmd="/bin/bash some_unrelated_script.sh"),
    )
    with patch(
        "app.modules.scraper.core.browser_reaper._list_processes", return_value=procs
    ), patch("app.modules.scraper.core.browser_reaper.os.kill") as mock_kill:
        result = reap_orphaned_browser_processes(min_age_seconds=0)

    assert result["trees_killed"] == 0
    mock_kill.assert_not_called()
