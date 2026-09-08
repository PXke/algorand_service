"""github_repository_contents: the model-supplied `path` is URL-encoded before hitting the authenticated GitHub API, not string-interpolated raw.

Regression test (2026-09-08, Fable audit): `path` used to be interpolated
unescaped into `https://api.github.com/repos/{slug}/contents/{path}` -- a
path containing "?"/"#"/".." segments could steer the request's query
string or path resolution in ways the model shouldn't control. Not SSRF
(the host is fixed), but a real request-shape-steering risk into an
authenticated call carrying GITHUB_TOKEN.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai import research_tools
from app.modules.ai.research_tools import _tool_github_repo_contents


def test_a_path_with_a_query_string_character_is_encoded_not_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A "?" in the path is percent-encoded, never treated as a real query-string start."""
    captured: list[str] = []

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        captured.append(url)
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(research_tools, "_github_get", fake_get)
    _tool_github_repo_contents("owner/repo", path="contracts/main.py?evil=1")

    assert len(captured) == 1
    # The literal "?" must be percent-encoded into the path, never treated
    # as the start of a real query string the caller didn't ask for.
    assert "contracts/main.py%3Fevil%3D1" in captured[0]
    assert "?evil=1" not in captured[0]


def test_a_normal_nested_path_still_resolves_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix must not break the ordinary case -- real directory separators stay real."""
    captured: list[str] = []

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        captured.append(url)
        return httpx.Response(
            200,
            json={"type": "file", "encoding": "base64", "content": ""},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(research_tools, "_github_get", fake_get)
    _tool_github_repo_contents("owner/repo", path="contracts/main.py")

    assert captured[0].endswith("/contents/contracts/main.py")
