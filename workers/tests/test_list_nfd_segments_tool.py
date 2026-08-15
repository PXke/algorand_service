"""list_nfd_segments: list the child segments (subdomains) issued under a parent Algorand NFD, e.g. every *.lumirogue.algo a project has handed out. Self-reported gap, 2026-08-13 (suggest_tool, LumiRogue session): search_nfd_directory only resolves one name at a time, with no way to answer "how many identities has this project actually issued"."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai import research_tools
from app.modules.ai.research_tools import _tool_list_nfd_segments
from app.modules.ai.research_tools import research_tools as research_tools_fn


def _json_response(url: str, status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


_PARENT_PAYLOAD = {
    "name": "lumirogue.algo",
    "appID": 3645521067,
    "owner": "LUMIWYDNAGSXEDVIGQTATT3UAJY2Y3YCWVFGLKPXCJTFPGTO5RUKIGIMBM",
    "properties": {"internal": {"segmentCount": "3"}},
}
_SEGMENTS_PAYLOAD = [
    {"name": "shark.lumirogue.algo", "owner": "OWNER1", "state": "owned", "expired": False},
    {"name": "tribtris.lumirogue.algo", "owner": "OWNER2", "state": "owned", "expired": False},
    {"name": "milva.lumirogue.algo", "owner": "OWNER3", "state": "owned", "expired": False},
]


def _fake_get_dispatch(
    *, parent_payload: object = None, parent_status: int = 200, segments_payload: object = None
) -> tuple:
    parent_payload = _PARENT_PAYLOAD if parent_payload is None else parent_payload
    segments_payload = _SEGMENTS_PAYLOAD if segments_payload is None else segments_payload
    seen: list[str] = []

    def _get(url: str, **_kw: object) -> httpx.Response:
        seen.append(url)
        if "/nfd/browse" in url:
            return _json_response(url, 200, segments_payload)
        return _json_response(url, parent_status, parent_payload)

    return _get, seen


def test_requires_parent_name() -> None:
    """An empty parent_name is a usage error, not an API call."""
    result = _tool_list_nfd_segments("")
    assert "error" in result


def test_lists_real_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: the parent resolves to an appID, browse returns its segments."""
    fake_get, _seen = _fake_get_dispatch()
    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_list_nfd_segments("lumirogue.algo")
    assert result["found"] is True
    names = {s["name"] for s in result["segments"]}
    assert names == {"shark.lumirogue.algo", "tribtris.lumirogue.algo", "milva.lumirogue.algo"}
    assert result["returned"] == 3


def test_surfaces_the_reported_total_independent_of_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent's own segmentCount is surfaced even when fewer rows are actually returned by browse (a capped limit)."""
    fake_get, _seen = _fake_get_dispatch(segments_payload=_SEGMENTS_PAYLOAD[:1])
    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_list_nfd_segments("lumirogue.algo", limit=1)
    assert result["returned"] == 1
    assert result["reported_total_segments"] == 3


def test_normalizes_bare_name_to_algo_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare name without the .algo suffix is normalized before the request."""
    fake_get, seen = _fake_get_dispatch()
    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    _tool_list_nfd_segments("lumirogue")
    assert any(u.endswith("/nfd/lumirogue.algo") for u in seen)


def test_parent_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 on the parent lookup is reported as not-found, not an error, and never reaches the browse call."""
    fake_get, seen = _fake_get_dispatch(parent_status=404, parent_payload={})
    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_list_nfd_segments("doesnotexist12345")
    assert result["found"] is False
    assert not any("/nfd/browse" in u for u in seen)


def test_parent_without_app_id_reports_error_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed/incomplete parent record (no appID) fails gracefully."""
    fake_get, _seen = _fake_get_dispatch(parent_payload={"name": "x.algo"})
    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_list_nfd_segments("x.algo")
    assert "error" in result


def test_no_segments_issued_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """A parent with zero segments returns an empty list, not an error."""
    fake_get, _seen = _fake_get_dispatch(
        parent_payload={
            "name": "freshname.algo",
            "appID": 111,
            "properties": {"internal": {"segmentCount": "0"}},
        },
        segments_payload=[],
    )
    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_list_nfd_segments("freshname.algo")
    assert result["found"] is True
    assert result["segments"] == []
    assert result["reported_total_segments"] == 0


def test_limit_is_capped_and_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The requested limit reaches the browse call, clamped into the 1-200 range."""
    seen_params: list[dict] = []

    def fake_get(url: str, **kw: object) -> httpx.Response:
        if "/nfd/browse" in url:
            seen_params.append(kw.get("params") or {})
            return _json_response(url, 200, _SEGMENTS_PAYLOAD)
        return _json_response(url, 200, _PARENT_PAYLOAD)

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    _tool_list_nfd_segments("lumirogue.algo", limit=999)
    assert seen_params[0]["limit"] == 200


def test_tool_registered() -> None:
    """Registers list_nfd_segments in both the tool schemas and handlers."""
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "list_nfd_segments" in names
    assert "list_nfd_segments" in handlers
