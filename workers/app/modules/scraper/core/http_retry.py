from __future__ import annotations

import time
from typing import Any

import httpx


class HttpRetryError(Exception):
    pass


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_attempts: int = 4,
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504}),
    **kwargs: Any,
) -> httpx.Response:
    """
    HTTP request with backoff on rate limits and transient errors.
    Honors Retry-After (seconds) when present (Discord/Reddit/CDN).
    """
    last_response: httpx.Response | None = None
    for attempt in range(max_attempts):
        response = client.request(method, url, **kwargs)
        last_response = response
        if response.status_code not in retry_statuses:
            return response

        if attempt >= max_attempts - 1:
            break

        wait = _retry_delay_seconds(response, attempt)
        time.sleep(wait)

    assert last_response is not None
    return last_response


def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(120.0, max(1.0, float(retry_after)))
        except ValueError:
            pass
    return min(60.0, 2.0**attempt)
