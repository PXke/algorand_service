"""lookup_arc69_metadata reads an ASA's ARC-69 attributes from the right place -- the note field of its most recent asset-config transaction, not lookup_asset's url field.

Root-caused live 2026-08-11 (Lumi Rogue "gungiELO" incident): the writer read
lookup_asset's url field (a bit.ly link to a plain PNG) and reported an
on-chain rating as unverifiable, when it was sitting in the note field of the
asset's config transaction the whole time -- the wrong field, for the wrong
reason (ARC-69 stores attributes in the note, url is only the artwork).
"""

from __future__ import annotations

import base64
import json

import pytest

from app.modules.ai import chain_tools


def _acfg(round_num: int, note_obj: dict | None) -> dict:
    note_b64 = base64.b64encode(json.dumps(note_obj).encode()).decode() if note_obj else None
    return {"confirmed-round": round_num, "round-time": 1700000000 + round_num, "note": note_b64}


def test_requires_numeric_asset_id() -> None:
    """A non-numeric asset_id is a usage error, not an indexer call."""
    result = chain_tools._tool_lookup_arc69_metadata("not-a-number")
    assert "error" in result


def test_no_acfg_transactions_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """An asset with no asset-config history at all is reported plainly."""
    monkeypatch.setattr(
        chain_tools, "_mainnet_idx_get", lambda _path, _params=None, **_kwargs: {"transactions": []}
    )
    result = chain_tools._tool_lookup_arc69_metadata(123)
    assert "error" in result


def test_uses_the_most_recent_config_not_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact Lumi Rogue shape: the mint's original config has no rating, a LATER manager update adds it -- must read the newest, not whichever the indexer happens to list first."""
    txns = [
        _acfg(100, {"standard": "arc69", "name": "Lumi Ankh 999", "properties": {}}),
        _acfg(
            500, {"standard": "arc69", "name": "Lumi Ankh 999", "properties": {"gungiELO": 1000}}
        ),
    ]
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, _params=None, **_kwargs: {"transactions": txns},
    )
    result = chain_tools._tool_lookup_arc69_metadata(3637325296)
    assert result["has_metadata"] is True
    assert result["metadata"]["properties"]["gungiELO"] == 1000
    assert result["round"] == 500


def test_no_note_on_latest_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """A config transaction with no note field at all -- reported, not a crash."""
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None, **_kwargs: {"transactions": [_acfg(1, None)]},  # noqa: ARG005
    )
    result = chain_tools._tool_lookup_arc69_metadata(1)
    assert result["has_metadata"] is False


def test_non_json_note_is_reported_not_crashed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A note field that isn't valid JSON (not ARC-69 formatted) fails soft."""
    bad = {"confirmed-round": 1, "round-time": 1, "note": base64.b64encode(b"not json").decode()}
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None, **_kwargs: {"transactions": [bad]},  # noqa: ARG005
    )
    result = chain_tools._tool_lookup_arc69_metadata(1)
    assert result["has_metadata"] is False
    assert "not ARC-69" in result["note"]


def test_an_indexer_error_is_not_reported_as_no_config_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (2026-09-08, Fable audit): an indexer error dict has no "transactions" key either -- it used to fall through to the same "no asset-config transactions found" message a real never-configured asset gets, an authoritative-sounding negative for what was actually an outage."""
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None, **_kwargs: {"error": "indexer timeout"},  # noqa: ARG005
    )
    result = chain_tools._tool_lookup_arc69_metadata(123)
    assert result["error"] == "indexer timeout"
    assert "no asset-config transactions found" not in result.get("error", "")


def test_pages_forward_to_find_the_true_most_recent_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (2026-09-08, Fable audit): the indexer returns this endpoint oldest-first with no descending option -- an asset reconfigured more than 10 times used to get the 10th-OLDEST config from a single page, not the actual most recent. Now pages forward until the indexer stops paginating."""
    # Page 1: rounds 1-10 (all with a stale 'v1' note). Page 2: round 11, the
    # true latest, with the real 'v2' note -- only reachable via pagination.
    page1_txns = [_acfg(i, {"standard": "arc69", "version": "v1"}) for i in range(1, 11)]
    page2_txns = [_acfg(11, {"standard": "arc69", "version": "v2"})]

    def fake_idx_get(_path: str, params: dict | None = None, **_kwargs: object) -> dict:
        if params and params.get("next"):
            return {"transactions": page2_txns}  # no next-token -- last page
        return {"transactions": page1_txns, "next-token": "page-2-token"}

    monkeypatch.setattr(chain_tools, "_mainnet_idx_get", fake_idx_get)
    result = chain_tools._tool_lookup_arc69_metadata(999)
    assert result["round"] == 11
    assert result["metadata"]["version"] == "v2"


def test_arc69_metadata_tool_registered() -> None:
    """Registers lookup_arc69_metadata in both the tool schemas and handlers."""
    schemas, handlers = chain_tools.chain_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "lookup_arc69_metadata" in names
    assert "lookup_arc69_metadata" in handlers
