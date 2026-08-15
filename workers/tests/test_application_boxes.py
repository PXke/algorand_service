"""application_boxes: a tool-gap suggestion from a real compose (ARC-89 registry, 2026-08-07 -- "could not read its application boxes to count how many ASAs have actually registered metadata... that would have quantified real-world adoption instead of relying only on npm download counts and spec status"). Counts and samples an app's box storage on mainnet or testnet."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.modules.ai import chain_tools as ct


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)  # type: ignore[arg-type]


class _FakeClient:
    def __init__(self, responder: Callable[[str, dict], _FakeResponse]) -> None:
        self._responder = responder

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        return self._responder(url, kwargs)


def _install_fake_httpx(
    monkeypatch: pytest.MonkeyPatch, responder: Callable[[str, dict], _FakeResponse]
) -> None:
    monkeypatch.setattr(httpx, "Client", lambda **_kw: _FakeClient(responder))


def test_counts_and_samples_boxes_under_the_request_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal, well-under-cap response returns the true count and up to max_boxes names."""
    boxes = [{"name": f"box-{i}"} for i in range(5)]

    def responder(url: str, kwargs: dict) -> _FakeResponse:
        assert "/v2/applications/753324084/boxes" in url
        assert kwargs["params"]["max"] == ct._BOXES_REQUEST_MAX
        return _FakeResponse(200, {"boxes": boxes})

    _install_fake_httpx(monkeypatch, responder)
    monkeypatch.setattr("app.core.config.ALGOD_URL", "https://mainnet-api.algonode.cloud")
    monkeypatch.setattr("app.core.config.ALGOD_TOKEN", "")

    result = ct._tool_application_boxes(753324084, network="mainnet", max_boxes=3)

    assert result["app_id"] == 753324084
    assert result["network"] == "mainnet"
    assert result["total_boxes"] == 5
    assert result["box_names"] == ["box-0", "box-1", "box-2"]


def test_reports_true_count_when_the_request_cap_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Algod's 400 'Result limit exceeded' still carries the true total-boxes count -- that must surface as total_boxes, not an error, with box_names empty and a note explaining why."""

    def responder(_url: str, _kwargs: dict) -> _FakeResponse:
        return _FakeResponse(400, {"message": "Result limit exceeded", "data": {"total-boxes": 59}})

    _install_fake_httpx(monkeypatch, responder)
    monkeypatch.setattr("app.core.config.TESTNET_ALGOD_URL", "https://testnet-api.algonode.cloud")

    result = ct._tool_application_boxes(753324084, network="testnet")

    assert result["total_boxes"] == 59
    assert result["box_names"] == []
    assert "59" in result["note"]
    assert "error" not in result


def test_testnet_uses_no_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """The public AlgoNode testnet endpoint needs no token, unlike the operator's mainnet node."""
    seen_headers = []

    def responder(_url: str, kwargs: dict) -> _FakeResponse:
        seen_headers.append(kwargs.get("headers"))
        return _FakeResponse(200, {"boxes": []})

    _install_fake_httpx(monkeypatch, responder)
    monkeypatch.setattr("app.core.config.TESTNET_ALGOD_URL", "https://testnet-api.algonode.cloud")

    ct._tool_application_boxes(1, network="testnet")

    assert seen_headers == [{}]


def test_mainnet_sends_the_configured_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator's own algod token is sent as X-Algo-API-Token for mainnet reads."""
    seen_headers = []

    def responder(_url: str, kwargs: dict) -> _FakeResponse:
        seen_headers.append(kwargs.get("headers"))
        return _FakeResponse(200, {"boxes": []})

    _install_fake_httpx(monkeypatch, responder)
    monkeypatch.setattr("app.core.config.ALGOD_URL", "https://mainnet-api.algonode.cloud")
    monkeypatch.setattr("app.core.config.ALGOD_TOKEN", "secret-token")

    ct._tool_application_boxes(1, network="mainnet")

    assert seen_headers == [{"X-Algo-API-Token": "secret-token"}]


def test_rejects_a_non_numeric_app_id() -> None:
    """A non-numeric app_id is rejected before any network call."""
    result = ct._tool_application_boxes("not-a-number")
    assert "error" in result


def test_rejects_an_unknown_network() -> None:
    """Only 'mainnet' and 'testnet' are accepted."""
    result = ct._tool_application_boxes(1, network="devnet")
    assert result == {"error": "network must be 'mainnet' or 'testnet'"}


def test_testnet_unconfigured_is_a_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset TESTNET_ALGOD_URL is a clean error, not a crash."""
    monkeypatch.setattr("app.core.config.TESTNET_ALGOD_URL", "")
    result = ct._tool_application_boxes(1, network="testnet")
    assert result == {"error": "testnet algod not configured (TESTNET_ALGOD_URL unset)"}


def test_max_boxes_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_boxes is clamped to [0, 100] regardless of what's requested."""
    boxes = [{"name": f"box-{i}"} for i in range(10)]

    def responder(_url: str, _kwargs: dict) -> _FakeResponse:
        return _FakeResponse(200, {"boxes": boxes})

    _install_fake_httpx(monkeypatch, responder)
    monkeypatch.setattr("app.core.config.ALGOD_URL", "https://mainnet-api.algonode.cloud")
    monkeypatch.setattr("app.core.config.ALGOD_TOKEN", "")

    result = ct._tool_application_boxes(1, network="mainnet", max_boxes=999)
    assert len(result["box_names"]) == 10  # only 10 boxes exist, clamp doesn't invent more

    result = ct._tool_application_boxes(1, network="mainnet", max_boxes=-5)
    assert result["box_names"] == []


def test_network_error_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport-level failure is caught and returned as an error, never raised."""

    class _BoomClient:
        def __enter__(self) -> _BoomClient:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def get(self, *_a: object, **_k: object) -> None:
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "Client", lambda **_kw: _BoomClient())
    monkeypatch.setattr("app.core.config.ALGOD_URL", "https://mainnet-api.algonode.cloud")
    monkeypatch.setattr("app.core.config.ALGOD_TOKEN", "")

    result = ct._tool_application_boxes(1, network="mainnet")
    assert "error" in result


def test_registered_in_chain_tools() -> None:
    """application_boxes is registered among the chain tool schemas and handlers."""
    from app.modules.ai.chain_tools import CHAIN_HANDLERS, CHAIN_SCHEMAS

    names = {s["function"]["name"] for s in CHAIN_SCHEMAS}
    assert "application_boxes" in names
    assert "application_boxes" in CHAIN_HANDLERS


def test_suggest_tool_resolves_box_suggestions_to_the_new_tool() -> None:
    """A future suggest_tool('testnet_app_box_contents', ...) resolves to application_boxes via the 'box' alias, instead of falsely matching testnet_lookup on the shared 'testnet' token (the exact miss this tool was built to fix)."""
    from app.modules.ai.writer_tools import _match_existing_tool

    known = {"testnet_lookup", "application_boxes", "lookup_application"}
    assert _match_existing_tool("testnet_app_box_contents", known) == "application_boxes"
