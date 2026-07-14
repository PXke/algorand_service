from __future__ import annotations

from app.modules.ai import chain_tools


def test_lookup_asset_computes_total_adjusted(monkeypatch) -> None:
    """Raw base-unit division must happen server-side, not be left to the
    model — this is exactly the arithmetic that went wrong in a real
    incident (2026-07-14): the writer manually converted a 15-digit raw ASA
    total and reported "1 trillion" for what should have been 1 billion."""
    monkeypatch.setattr(
        chain_tools,
        "_algod_get",
        lambda path: {
            "params": {
                "name": "CompX Token",
                "unit-name": "COMPX",
                "total": 1_000_000_000_000_000,
                "decimals": 6,
                "creator": "CREATOR",
            }
        },
    )
    result = chain_tools._tool_lookup_asset(1732165149)
    assert result["total"] == 1_000_000_000_000_000
    assert result["decimals"] == 6
    assert result["total_adjusted"] == 1_000_000_000.0


def test_lookup_asset_total_adjusted_none_when_decimals_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        chain_tools,
        "_algod_get",
        lambda path: {"params": {"total": 1000}},
    )
    result = chain_tools._tool_lookup_asset(1)
    assert result["total_adjusted"] is None


def test_get_asset_holder_share_computes_real_percentage(monkeypatch) -> None:
    """Regression-pin the actual CompX incident numbers: total=1e15 raw
    (decimals=6 -> 1 billion COMPX), creator holds 112,111,670,453,492 raw
    -> the real share is ~11.21%, NOT the "99.99%" the writer fabricated by
    doing the division itself and getting it wrong."""

    def fake_algod_get(path: str):
        if path.startswith("/v2/assets/"):
            return {
                "params": {
                    "name": "CompX Token",
                    "total": 1_000_000_000_000_000,
                    "decimals": 6,
                    "creator": "CREATOR_ADDR",
                }
            }
        if path.startswith("/v2/accounts/"):
            return {
                "amount": 65_365_797,
                "status": "Offline",
                "assets": [
                    {"asset-id": 1732165149, "amount": 112_111_670_453_492},
                    {"asset-id": 999, "amount": 5},
                ],
            }
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(chain_tools, "_algod_get", fake_algod_get)

    result = chain_tools._tool_get_asset_holder_share(1732165149, "CREATOR_ADDR")
    assert result["total_supply_adjusted"] == 1_000_000_000.0
    assert result["holder_amount_adjusted"] == 112_111_670.453492
    assert result["share_pct"] == 11.2112
    assert result["share_pct"] != 99.99


def test_get_asset_holder_share_zero_when_address_does_not_hold_asset(monkeypatch) -> None:
    def fake_algod_get(path: str):
        if path.startswith("/v2/assets/"):
            return {"params": {"total": 1000, "decimals": 0, "creator": "X"}}
        if path.startswith("/v2/accounts/"):
            return {"amount": 0, "status": "Offline", "assets": []}
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(chain_tools, "_algod_get", fake_algod_get)

    result = chain_tools._tool_get_asset_holder_share(1, "SOME_ADDR")
    assert result["share_pct"] == 0.0


def test_get_asset_holder_share_requires_address() -> None:
    result = chain_tools._tool_get_asset_holder_share(1, "")
    assert "error" in result


def test_get_asset_holder_share_propagates_asset_error(monkeypatch) -> None:
    monkeypatch.setattr(chain_tools, "_algod_get", lambda path: {"_status": 404})
    result = chain_tools._tool_get_asset_holder_share(999, "ADDR")
    assert "error" in result


def test_asset_holder_share_tool_registered() -> None:
    schemas, handlers = chain_tools.chain_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "get_asset_holder_share" in names
    assert "get_asset_holder_share" in handlers


def test_lookup_asset_by_name_returns_candidates(monkeypatch) -> None:
    """algod's lookup_asset needs a numeric id and can't search by name — this
    is the tool a stronger research model explicitly asked for (suggest_tool,
    2026-07-14) when a project's asset_id guess 404'd. Uses the mainnet
    indexer, mirroring the existing testnet_lookup pattern."""
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda path, params=None: {
            "assets": [
                {
                    "index": 1732165149,
                    "params": {
                        "name": "CompX Token",
                        "unit-name": "COMPX",
                        "creator": "CREATOR_ADDR",
                    },
                }
            ]
        },
    )
    result = chain_tools._tool_lookup_asset_by_name("COMPX")
    assert result["query"] == "COMPX"
    assert result["results"] == [
        {
            "asset_id": 1732165149,
            "name": "CompX Token",
            "unit_name": "COMPX",
            "creator": "CREATOR_ADDR",
        }
    ]


def test_lookup_asset_by_name_requires_nonempty_name() -> None:
    result = chain_tools._tool_lookup_asset_by_name("")
    assert "error" in result


def test_lookup_asset_by_name_propagates_indexer_error(monkeypatch) -> None:
    monkeypatch.setattr(
        chain_tools, "_mainnet_idx_get", lambda path, params=None: {"error": "timeout"}
    )
    result = chain_tools._tool_lookup_asset_by_name("COMPX")
    assert result["error"] == "timeout"


def test_lookup_asset_by_name_tool_registered() -> None:
    schemas, handlers = chain_tools.chain_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "lookup_asset_by_name" in names
    assert "lookup_asset_by_name" in handlers
