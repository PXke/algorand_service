from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def fetch_algod_status(*, timeout: float = 8.0) -> dict[str, Any]:
    """Best-effort Algod /v2/status for chain activity tiles."""
    url = settings.algod_url.rstrip("/") + "/v2/status"
    headers: dict[str, str] = {}
    token = settings.algod_token.strip()
    if token:
        headers["X-Algo-API-Token"] = token

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return {}
            return payload
    except Exception as exc:
        logger.warning("Algod status fetch failed: %s", exc)
        return {}
