"""trace_asset_creator resolves an ASA's real creator identity (NFD name, other assets minted, top holders) so a name-matched asset isn't attributed to an entity on a coincidental name match alone -- root-caused on the Lumi Rogue LUMI-token incident (2026-08-11), where both Mistral and DeepSeek reported an unrelated token as the project's own."""

from __future__ import annotations

import pytest

from app.modules.ai import chain_tools


def test_trace_asset_creator_requires_asset_lookup_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed lookup_asset (e.g. 404) propagates as-is, no further calls made."""
    monkeypatch.setattr(
        chain_tools, "_tool_lookup_asset", lambda _aid: {"error": "asset not found"}
    )
    result = chain_tools._tool_trace_asset_creator(999)
    assert result == {"error": "asset not found"}


def test_trace_asset_creator_requires_a_creator_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """An asset with no creator on record is a clean error, not a crash."""
    monkeypatch.setattr(
        chain_tools, "_tool_lookup_asset", lambda _aid: {"asset_id": 1, "creator": None}
    )
    result = chain_tools._tool_trace_asset_creator(1)
    assert "error" in result


def test_trace_asset_creator_resolves_nfd_name_and_other_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: creator's NFD name, prior-mint count, and top holders all surface in one call."""
    monkeypatch.setattr(
        chain_tools,
        "_tool_lookup_asset",
        lambda _aid: {
            "asset_id": 296009253,
            "name": "LUMI",
            "unit_name": "LUMI",
            "creator": "UNRELATEDCREATORADDR",
        },
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._tool_search_nfd_directory",
        lambda address="", **kw: {"address": address, "found": True, "name": "somebody.algo"},  # noqa: ARG005
    )
    monkeypatch.setattr(
        chain_tools,
        "_tool_lookup_account",
        lambda _addr: {"total_created_assets": 47},
    )
    monkeypatch.setattr(
        chain_tools,
        "_tool_lookup_asset_holders",
        lambda _aid, limit=5: {  # noqa: ARG005
            "top_holders": [{"address": "UNRELATEDCREATORADDR", "amount_adjusted": 780_000_000}]
        },
    )

    result = chain_tools._tool_trace_asset_creator(296009253)

    assert result["creator_address"] == "UNRELATEDCREATORADDR"
    assert result["creator_nfd_name"] == "somebody.algo"
    assert result["creator_total_assets_created"] == 47
    assert result["top_holders"][0]["address"] == "UNRELATEDCREATORADDR"
    assert "not affiliation" in result["hint"]


def test_trace_asset_creator_handles_no_nfd_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """A creator address with no registered NFD name returns None, not an error -- most addresses have no .algo name."""
    monkeypatch.setattr(
        chain_tools,
        "_tool_lookup_asset",
        lambda _aid: {"asset_id": 1, "name": "X", "creator": "SOMEADDR"},
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._tool_search_nfd_directory",
        lambda address="", **kw: {"address": address, "found": False},  # noqa: ARG005
    )
    monkeypatch.setattr(chain_tools, "_tool_lookup_account", lambda _addr: {"total_created_assets": 1})
    monkeypatch.setattr(
        chain_tools, "_tool_lookup_asset_holders", lambda _aid, limit=5: {"top_holders": []}  # noqa: ARG005
    )

    result = chain_tools._tool_trace_asset_creator(1)
    assert result["creator_nfd_name"] is None


def test_trace_asset_creator_tool_registered() -> None:
    """Registers trace_asset_creator in both the tool schemas and handlers."""
    schemas, handlers = chain_tools.chain_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "trace_asset_creator" in names
    assert "trace_asset_creator" in handlers
