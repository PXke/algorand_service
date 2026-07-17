"""RobynAdapter: bridges robyn.Request to x402's HTTPAdapter protocol
(x402.http.types.HTTPAdapter, structural typing — no base class to inherit).

No official x402 adapter ships for Robyn (only Flask/FastAPI), so this is the
seam that makes the rest of the x402 package's HTTP-flow code reusable here.
"""

from __future__ import annotations

from typing import Any

from robyn import Request


class RobynAdapter:
    def __init__(self, request: Request) -> None:
        self._request = request

    def get_header(self, name: str) -> str | None:
        return self._request.headers.get(name)

    def get_method(self) -> str:
        return self._request.method

    def get_path(self) -> str:
        return self._request.url.path

    def get_url(self) -> str:
        url = self._request.url
        return f"{url.scheme}://{url.host}{url.path}"

    def get_accept_header(self) -> str:
        return self._request.headers.get("Accept") or ""

    def get_user_agent(self) -> str:
        return self._request.headers.get("User-Agent") or ""

    def get_query_params(self) -> dict[str, Any] | None:
        return self._request.query_params.to_dict()

    def get_query_param(self, name: str) -> str | None:
        return self._request.query_params.get(name)

    def get_body(self) -> Any:
        return None
