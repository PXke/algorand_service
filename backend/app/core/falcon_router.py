"""Falcon router adapter for register_*_routes functions."""

from __future__ import annotations

import inspect
import json
import re
from types import SimpleNamespace
from typing import Any, Callable

import falcon

from app.core.http import QueryParams, Request

Handler = Callable[[Request], Any]
_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def _to_falcon_path(path: str) -> str:
    return _PARAM_RE.sub(r"{\1}", path)


def _apply_result(resp: falcon.Response, result: Any) -> None:
    if result is None:
        if resp.media is None and resp.text is None and resp.data is None:
            resp.status = falcon.HTTP_204
        return

    if hasattr(result, "status_code") and hasattr(result, "headers") and hasattr(result, "description"):
        resp.status = falcon.code_to_http_status(int(result.status_code or 200))
        for key, value in (result.headers or {}).items():
            resp.set_header(str(key), str(value))
        description = result.description
        if isinstance(description, bytes):
            resp.data = description
        else:
            resp.text = "" if description is None else str(description)
        return

    if isinstance(result, (dict, list)):
        resp.media = result
        return
    if isinstance(result, bytes):
        resp.data = result
        return
    if isinstance(result, str):
        resp.text = result
        return

    resp.text = json.dumps(result, default=str)
    resp.content_type = "application/json"


class _FalconResource:
    def __init__(self, methods: dict[str, Handler]) -> None:
        self._methods = methods

    def _handle(self, req: falcon.Request, resp: falcon.Response, **kwargs: str) -> None:
        handler = self._methods.get(req.method.upper())
        if handler is None:
            raise falcon.HTTPMethodNotAllowed(list(self._methods.keys()))
        # Falcon exposes headers as uppercase (e.g. X-SESSION-TOKEN). Route
        # handlers expect lowercase / mixed-case keys from the old stack.
        headers = {str(k).lower(): str(v) for k, v in req.headers.items()}
        request = Request(
            method=req.method,
            headers=headers,
            query_params=QueryParams(req.params),
            path_params=kwargs,
            body=req.bounded_stream.read(),
            url=SimpleNamespace(path=req.path, host=req.host, scheme=req.scheme),
        )
        out = handler(request)
        if inspect.isawaitable(out):
            raise TypeError("async handlers are not supported under Falcon")
        _apply_result(resp, out)

    on_get = _handle
    on_post = _handle
    on_patch = _handle
    on_delete = _handle
    on_put = _handle
    on_head = _handle


class FalconRouter:
    """Decorator-style router compatible with existing `register_*_routes` APIs."""

    def __init__(self, app: falcon.App) -> None:
        self._app = app
        self._routes: dict[str, dict[str, Handler]] = {}

    def _register(self, method: str, path: str, handler: Handler) -> Handler:
        falcon_path = _to_falcon_path(path)
        methods = self._routes.setdefault(falcon_path, {})
        methods[method] = handler
        self._app.add_route(falcon_path, _FalconResource(methods))
        return handler

    def _decorator(self, method: str, path: str):
        def inner(handler: Handler) -> Handler:
            return self._register(method, path, handler)

        return inner

    def get(self, path: str):
        return self._decorator("GET", path)

    def post(self, path: str):
        return self._decorator("POST", path)

    def patch(self, path: str):
        return self._decorator("PATCH", path)

    def delete(self, path: str):
        return self._decorator("DELETE", path)

    def put(self, path: str):
        return self._decorator("PUT", path)

    def head(self, path: str):
        return self._decorator("HEAD", path)
