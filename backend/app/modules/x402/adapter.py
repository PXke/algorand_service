"""PlatformHTTPAdapter: bridges this backend's framework-neutral Request (app.core.http) to x402's HTTPAdapter protocol (x402.http.types.HTTPAdapter, structural typing — no base class to inherit).

No official x402 adapter ships for this backend's own request type (only
Flask/FastAPI), so this is the seam that makes the rest of the x402
package's HTTP-flow code reusable here. Originally written against the
Robyn framework (pre-Falcon-migration Request shape) -- renamed 2026-08-15
since the migration's own app.core.http.Request abstraction is already
framework-neutral, and "RobynAdapter" had gone stale/misleading.
"""

from __future__ import annotations

from typing import Any

from app.core.http import Request


class PlatformHTTPAdapter:
    """Bridges this backend's Request type to x402's HTTPAdapter protocol."""

    def __init__(self, request: Request) -> None:
        """Wrap a platform Request for the x402 HTTPAdapter protocol."""
        self._request = request

    def get_header(self, name: str) -> str | None:
        """Return a request header by name, or None if absent."""
        return self._request.headers.get(name) or self._request.headers.get(name.lower())

    def get_method(self) -> str:
        """Return the request's HTTP method."""
        return self._request.method

    def get_path(self) -> str:
        """Return the request's URL path."""
        return self._request.url.path

    def get_url(self) -> str:
        """Return the request's full URL (scheme + host + path)."""
        url = self._request.url
        return f"{url.scheme}://{url.host}{url.path}"

    def get_accept_header(self) -> str:
        """Return the request's Accept header, or empty string if absent."""
        return self.get_header("Accept") or ""

    def get_user_agent(self) -> str:
        """Return the request's User-Agent header, or empty string if absent."""
        return self.get_header("User-Agent") or ""

    def get_query_params(self) -> dict[str, Any] | None:
        """Return the request's query parameters as a dict."""
        return self._request.query_params.to_dict()

    def get_query_param(self, name: str) -> str | None:
        """Return a single query parameter by name, or None if absent."""
        return self._request.query_params.get(name)

    def get_body(self) -> Any:  # noqa: ANN401 -- matches x402.http.types.HTTPAdapter protocol's get_body() shape
        """Return the request body. Always None; this adapter never needs it."""
        return None
