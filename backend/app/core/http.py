"""Framework-neutral HTTP types used by route modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Protocol


class QueryParams(dict[str, Any]):
    """Mapping wrapper with a `to_dict()` method for legacy adapters."""

    def to_dict(self) -> dict[str, Any]:
        return dict(self)


@dataclass(slots=True)
class Request:
    """Minimal request shape consumed by existing handlers."""

    method: str
    headers: dict[str, str]
    query_params: QueryParams
    path_params: dict[str, str]
    body: bytes = b""
    url: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(path="", host="", scheme="http"))


@dataclass(slots=True)
class Response:
    """Framework-neutral response container."""

    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    description: Any = ""


class Router(Protocol):
    """Subset of router API used by register_*_routes modules."""

    def get(self, path: str): ...
    def post(self, path: str): ...
    def patch(self, path: str): ...
    def delete(self, path: str): ...
    def put(self, path: str): ...
    def head(self, path: str): ...
