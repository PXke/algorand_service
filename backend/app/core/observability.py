"""Bugsnag error reporting for the Robyn backend.

Attaches a logging handler so Robyn's logged route errors (and any ERROR-level
log with a traceback) are reported automatically. Configurable via env
BUGSNAG_API_KEY / BUGSNAG_RELEASE_STAGE; safe no-op if the package or key is
absent.
"""

from __future__ import annotations

import logging
import os

_DEFAULT_KEY = "b83be2212bf6cbca2e5abc3510f91210"

logger = logging.getLogger(__name__)


def init_bugsnag(*, project_root: str = "", release_stage: str = "prod") -> None:
    try:
        key = os.getenv("BUGSNAG_API_KEY", _DEFAULT_KEY).strip()
        if not key:
            return
        import bugsnag
        from bugsnag.handlers import BugsnagHandler

        bugsnag.configure(
            api_key=key,
            project_root=project_root or os.getcwd(),
            release_stage=os.getenv("BUGSNAG_RELEASE_STAGE", release_stage),
            auto_capture_sessions=True,
        )
        handler = BugsnagHandler()
        handler.setLevel(logging.ERROR)
        logging.getLogger().addHandler(handler)
    except Exception:
        # Never let observability setup break the app.
        logger.warning("bugsnag setup failed; error reporting disabled", exc_info=True)
