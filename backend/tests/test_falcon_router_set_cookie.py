"""falcon_router._apply_result: a handler-set "Set-Cookie" response header.

Regression test for the 2026-09-08 incident: every route returning a
Set-Cookie header via the shared Response(headers={...}) shape (auth's
verify-wallet-signature and logout, both minting/clearing the
cross-subdomain session cookie) 500'd in prod with
falcon.errors.HeaderNotSupported -- Falcon's resp.set_header() explicitly
refuses "Set-Cookie" (a response can carry more than one), and the
generic header-application loop in _apply_result used set_header() for
every header uniformly. This never surfaced in test_auth_routes.py
because those tests call auth_verify()/auth_logout() directly and only
inspect the returned dataclass -- they never route through the real
Falcon dispatch path (_FalconResource._handle -> _apply_result) that
actually calls resp.set_header(), so the bug was invisible to them.
"""

from __future__ import annotations

import falcon
from falcon import testing

from app.core.falcon_router import FalconRouter
from app.core.http import Request, Response


def _make_app() -> falcon.App:
    app = falcon.App()
    router = FalconRouter(app)

    @router.post("/set-cookie")
    def set_cookie(_request: Request) -> Response:
        return Response(
            status_code=200,
            headers={
                "Content-Type": "application/json",
                "Set-Cookie": "wallet_session=tok; Path=/",
            },
            description='{"ok":true}',
        )

    return app


def test_a_handler_returned_set_cookie_header_does_not_500() -> None:
    """A route returning Set-Cookie in its Response.headers must not raise HeaderNotSupported when dispatched through the real Falcon app."""
    client = testing.TestClient(_make_app())
    resp = client.simulate_post("/set-cookie")
    assert resp.status_code == 200


def test_the_set_cookie_header_actually_reaches_the_response() -> None:
    """Not just "doesn't crash" -- the cookie value itself must actually be set on the response, not silently dropped."""
    client = testing.TestClient(_make_app())
    resp = client.simulate_post("/set-cookie")
    assert "wallet_session=tok" in resp.headers.get("Set-Cookie", "")
