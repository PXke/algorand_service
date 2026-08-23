"""Public algod proxy: allowlisted paths, token stays server-side."""

from __future__ import annotations

import pytest
from falcon import testing

from app.falcon_main import create_app
from app.modules.chain import algod_proxy


@pytest.mark.parametrize(
    ("method", "path", "ok"),
    [
        ("GET", "v2/transactions/params", True),
        ("GET", "v2/status", True),
        ("GET", "genesis", True),
        ("GET", "v2/ledger/supply", True),
        ("GET", "v2/accounts/RR4FHUPJE32YGJMS76VPI5MZ2VMCGKTPEGPYORGS3IXJ63U2CVNDUHQLS4", True),
        ("POST", "v2/transactions", True),
        ("GET", "v2/shutdown", False),
        ("POST", "v2/status", False),
        ("GET", "v2/blocks/../status", False),
        ("GET", "", False),
    ],
)
def test_allowed_algod_path(method: str, path: str, ok: bool) -> None:
    assert algod_proxy.allowed_algod_path(method, path) is ok


def test_proxy_forwards_params(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_forward(method: str, path: str, *, body: bytes = b"", query: str = "") -> tuple[int, bytes, str]:
        assert method == "GET"
        assert path == "v2/transactions/params"
        return 200, b'{"min-fee":1000}', "application/json"

    monkeypatch.setattr(algod_proxy, "forward_algod", fake_forward)
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/api/v1/algod/v2/transactions/params")
    assert resp.status_code == 200
    assert resp.json["min-fee"] == 1000


def test_proxy_rejects_admin_path() -> None:
    client = testing.TestClient(create_app())
    resp = client.simulate_get("/api/v1/algod/v2/shutdown")
    assert resp.status_code == 404
    assert resp.json["error"]["code"] == "not_found"
