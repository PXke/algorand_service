"""Which x402-related admin routes register_admin_routes still mounts."""

from __future__ import annotations

from collections.abc import Callable

from app.modules.admin.api import routes as admin_routes


class _RecordingRouter:
    """Records (method, path) for every registration; satisfies app.core.http.Router."""

    def __init__(self) -> None:
        self.routes: set[tuple[str, str]] = set()

    def _register(self, method: str, path: str) -> Callable[[Callable], Callable]:
        self.routes.add((method, path))
        return lambda handler: handler

    def get(self, path: str) -> Callable[[Callable], Callable]:
        return self._register("GET", path)

    def post(self, path: str) -> Callable[[Callable], Callable]:
        return self._register("POST", path)

    def patch(self, path: str) -> Callable[[Callable], Callable]:
        return self._register("PATCH", path)

    def delete(self, path: str) -> Callable[[Callable], Callable]:
        return self._register("DELETE", path)

    def put(self, path: str) -> Callable[[Callable], Callable]:
        return self._register("PUT", path)


def test_social_moderation_route_is_gone_but_storage_admin_routes_stay() -> None:
    """The x402 social network was removed (2026-09-10 consolidation): its admin lever must no longer be mounted, while the storage inspect/remove routes are untouched."""
    router = _RecordingRouter()
    admin_routes.register_admin_routes(router)

    assert ("POST", "/api/v1/admin/x402-social/moderation/remove") not in router.routes
    assert not hasattr(admin_routes, "admin_x402_social_moderation_remove")
    assert ("GET", "/api/v1/admin/x402-storage/inspect") in router.routes
    assert ("POST", "/api/v1/admin/x402-storage/remove") in router.routes
