"""Beat task: POST the API-host internal reap route for expired-past-grace backups.

The local-disk connector lives on the API host (gunicorn), not in this
worker process, so this beat never unlinks files itself -- it asks the
process that actually holds the disk. Token-gated; skipped when the token
is empty. Hits 127.0.0.1 by default (bypasses nginx), so net_guard is not
used: that helper rejects loopback on purpose.
"""

from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.core.config import (
    X402_STORAGE_REAPER_TIMEOUT_SECONDS,
    X402_STORAGE_REAPER_TOKEN,
    X402_STORAGE_REAPER_URL,
)
from app.core.http_client import get_http_client
from app.core.redis_lock import single_flight

logger = logging.getLogger(__name__)

_HEADER = "X-Storage-Reaper-Token"


@celery_app.task(name="app.tasks.x402_storage_reaper.reap_expired_backups")
@single_flight(lambda *_a, **_kw: "x402_storage:reap", ttl=celery_app.conf.task_time_limit)
def reap_expired_backups() -> dict[str, object]:
    """Trigger one reaper tick on the API host. Honours an empty token so a manual trigger stays off too."""
    token = X402_STORAGE_REAPER_TOKEN.strip()
    if not token:
        return {"status": "skipped", "reason": "x402_storage_reaper_token_empty"}
    url = X402_STORAGE_REAPER_URL.strip()
    if not url:
        return {"status": "skipped", "reason": "x402_storage_reaper_url_empty"}
    try:
        response = get_http_client(
            timeout=X402_STORAGE_REAPER_TIMEOUT_SECONDS, follow_redirects=False
        ).post(url, headers={_HEADER: token})
    except Exception as exc:
        logger.warning("x402 storage reaper: POST to API host failed: %s", exc, exc_info=True)
        return {"status": "error", "reason": "request_failed"}
    if response.status_code != 200:
        logger.warning(
            "x402 storage reaper: API host returned %s body=%s",
            response.status_code,
            response.text[:500],
        )
        return {"status": "error", "http_status": response.status_code}
    try:
        payload = response.json()
    except Exception:
        logger.warning("x402 storage reaper: API host returned non-JSON", exc_info=True)
        return {"status": "error", "reason": "invalid_json"}
    if not isinstance(payload, dict):
        return {"status": "error", "reason": "invalid_json"}
    return payload
