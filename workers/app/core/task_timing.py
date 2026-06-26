from __future__ import annotations

import logging
import time
from typing import Any

from celery.signals import task_failure, task_postrun, task_prerun

logger = logging.getLogger("workers.task_timing")

_task_start: dict[str, float] = {}


@task_prerun.connect
def _on_task_prerun(task_id: str | None = None, task: Any = None, **_: Any) -> None:
    if task_id:
        _task_start[task_id] = time.monotonic()


@task_postrun.connect
def _on_task_postrun(
    task_id: str | None = None,
    task: Any = None,
    state: str | None = None,
    **_: Any,
) -> None:
    if not task_id:
        return
    started = _task_start.pop(task_id, None)
    if started is None:
        return
    elapsed_ms = int((time.monotonic() - started) * 1000)
    name = getattr(task, "name", "unknown")
    logger.info("celery_task_done task=%s state=%s duration_ms=%d", name, state, elapsed_ms)


@task_failure.connect
def _on_task_failure(
    task_id: str | None = None,
    task: Any = None,
    exception: BaseException | None = None,
    **_: Any,
) -> None:
    name = getattr(task, "name", "unknown")
    logger.warning(
        "celery_task_failed task=%s task_id=%s error=%s",
        name,
        task_id,
        exception,
    )
