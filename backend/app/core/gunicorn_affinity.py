"""Gunicorn gthread worker that pins each pool thread to one CPU core."""

from __future__ import annotations

from concurrent import futures

from gunicorn.workers.gthread import ThreadWorker

from app.core.cpu_affinity import pin_next_pool_thread


class AffinityThreadWorker(ThreadWorker):
    """Like gthread, but each executor thread is bound to a distinct core."""

    def get_thread_pool(self) -> futures.ThreadPoolExecutor:
        return futures.ThreadPoolExecutor(
            max_workers=self.cfg.threads,
            initializer=pin_next_pool_thread,
            thread_name_prefix="gthread-cpu",
        )
