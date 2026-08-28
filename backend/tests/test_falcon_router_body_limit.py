"""falcon_router._FalconResource._handle: request body size cap.

Content-Length is rejected with 413 before the body stream is ever read,
with a larger cap for the admin PATCH route (the only PATCH route
registered anywhere in the backend).
"""

from __future__ import annotations

import falcon
from falcon import testing

from app.core.falcon_router import (
    _MAX_ADMIN_PATCH_BODY_BYTES,
    _MAX_BODY_BYTES,
    FalconRouter,
)
from app.core.http import Request


def _make_app() -> falcon.App:
    app = falcon.App()
    router = FalconRouter(app)

    @router.post("/echo")
    def echo_post(request: Request) -> dict:
        return {"len": len(request.body)}

    @router.patch("/echo")
    def echo_patch(request: Request) -> dict:
        return {"len": len(request.body)}

    return app


def test_post_body_at_default_cap_is_accepted() -> None:
    """A body exactly at the default 256 KB cap is accepted."""
    client = testing.TestClient(_make_app())
    resp = client.simulate_post("/echo", body=b"x" * _MAX_BODY_BYTES)
    assert resp.status_code == 200
    assert resp.json["len"] == _MAX_BODY_BYTES


def test_post_body_over_default_cap_rejected_413_before_reading_body() -> None:
    """A body one byte over the default cap 413s off Content-Length alone."""
    client = testing.TestClient(_make_app())
    resp = client.simulate_post("/echo", body=b"x" * (_MAX_BODY_BYTES + 1))
    assert resp.status_code == 413


def test_patch_body_over_default_cap_but_under_admin_patch_cap_is_accepted() -> None:
    """PATCH gets the larger 2 MB cap (the admin article-edit endpoint)."""
    client = testing.TestClient(_make_app())
    body = b"x" * (_MAX_BODY_BYTES + 1)
    assert len(body) < _MAX_ADMIN_PATCH_BODY_BYTES
    resp = client.simulate_patch("/echo", body=body)
    assert resp.status_code == 200
    assert resp.json["len"] == len(body)


def test_patch_body_over_admin_patch_cap_rejected_413() -> None:
    """A PATCH body over the larger admin cap still 413s."""
    client = testing.TestClient(_make_app())
    resp = client.simulate_patch("/echo", body=b"x" * (_MAX_ADMIN_PATCH_BODY_BYTES + 1))
    assert resp.status_code == 413
