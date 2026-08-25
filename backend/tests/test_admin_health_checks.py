"""Per-check System-tab health endpoint: dispatches by name so the frontend can fetch each check independently instead of blocking on one combined `/health/ready` call."""

from __future__ import annotations

from unittest.mock import patch

from app.core.health import CHECKS, CheckResult
from app.core.http import Request
from app.modules.admin.api import routes as admin_routes


def _req(name: str) -> Request:
    return Request(
        method="GET",
        headers={},
        query_params={},  # type: ignore[arg-type]
        path_params={"name": name},
    )


def test_admin_health_check_requires_admin_wallet() -> None:
    """No admin wallet -> whatever require_admin_wallet returns, not a check result."""
    resp = admin_routes.admin_health_check(_req("redis"))
    assert getattr(resp, "status_code", 200) != 200


def test_admin_health_check_unknown_name_404s() -> None:
    """A name outside CHECKS 404s cleanly instead of KeyError -> 500."""
    with patch.object(admin_routes, "require_admin_wallet", return_value=None):
        resp = admin_routes.admin_health_check(_req("not_a_real_check"))
    assert resp.status_code == 404


def test_admin_health_check_dispatches_by_name() -> None:
    """Each registered check name runs only its own check function, not the whole batch."""
    with (
        patch.object(admin_routes, "require_admin_wallet", return_value=None),
        patch.dict(
            CHECKS,
            {"redis": lambda: CheckResult("redis", True, "pong")},
        ),
    ):
        result = admin_routes.admin_health_check(_req("redis"))
    assert result == {"name": "redis", "ok": True, "detail": "pong"}


def test_checks_registry_covers_every_readiness_check() -> None:
    """CHECKS stays in sync with the names run_readiness_checks() combines for /health/ready."""
    from app.core.health import run_readiness_checks

    names = {c.name for c in run_readiness_checks()}
    assert names == set(CHECKS)
