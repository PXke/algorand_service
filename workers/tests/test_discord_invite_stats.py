"""lookup_discord_invite_stats: real member/online counts for a Discord server via its public invite-preview endpoint.

Regression tests (2026-09-08, Fable audit): the tool used to read counts
from `profile.member_count`/`profile.online_count`, an object that only
exists for a server with Discord's "Server Profile" feature enabled (large,
discoverable communities). Discord's own `with_counts=true` contract always
populates the TOP-LEVEL `approximate_member_count`/`approximate_presence_count`
fields instead -- confirmed live against the real API. A typical small
project server (exactly what this tool is for) has no `profile` object at
all and silently returned `None` for both counts before this fix.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai import research_tools
from app.modules.ai.research_tools import _tool_lookup_discord_invite_stats


def _json_response(url: str, status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


def test_reads_top_level_approximate_counts_when_no_profile_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typical small project server has no "profile" object -- the top-level approximate_* fields are the only reliable source."""
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **_kw: _json_response(
            url,
            200,
            {
                "guild": {"name": "Small Project", "description": "d"},
                "approximate_member_count": 342,
                "approximate_presence_count": 12,
                # no "profile" key at all
            },
        ),
    )
    result = _tool_lookup_discord_invite_stats("discord.gg/smallproject")
    assert result["exists"] is True
    assert result["member_count"] == 342
    assert result["online_count"] == 12


def test_falls_back_to_profile_when_top_level_counts_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the top-level fields are genuinely missing (not just zero), profile's own counts are still used rather than reporting None."""
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **_kw: _json_response(
            url,
            200,
            {
                "guild": {"name": "Large Community"},
                "profile": {"member_count": 23649, "online_count": 1231},
            },
        ),
    )
    result = _tool_lookup_discord_invite_stats("discord.gg/algorand")
    assert result["member_count"] == 23649
    assert result["online_count"] == 1231


def test_extracts_invite_code_from_full_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """A full discord.gg URL is parsed down to the bare invite code."""
    captured: list[str] = []

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        captured.append(url)
        return _json_response(url, 200, {"guild": {"name": "X"}, "approximate_member_count": 1})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    _tool_lookup_discord_invite_stats("https://discord.gg/algorand")
    assert any("/invites/algorand" in c for c in captured)


def test_nonexistent_invite_reports_exists_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 from Discord's invite endpoint is a genuine "invite not found or expired"."""
    monkeypatch.setattr(
        research_tools, "_guarded_get", lambda url, **_kw: _json_response(url, 404, {})
    )
    result = _tool_lookup_discord_invite_stats("discord.gg/nonexistent")
    assert result["exists"] is False


def test_requires_a_parseable_invite() -> None:
    """An empty/unparseable invite is a usage error, not a network call."""
    result = _tool_lookup_discord_invite_stats("")
    assert "error" in result
