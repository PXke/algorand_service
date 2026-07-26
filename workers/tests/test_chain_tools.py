"""On-chain lookup tools compute real percentages, never the writer's own arithmetic."""

from __future__ import annotations

import pytest

from app.modules.ai import chain_tools


def test_lookup_asset_computes_total_adjusted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw base-unit division must happen server-side, not be left to the model — this is exactly the arithmetic that went wrong in a real incident (2026-07-14): the writer manually converted a 15-digit raw ASA total and reported "1 trillion" for what should have been 1 billion."""
    monkeypatch.setattr(
        chain_tools,
        "_algod_get",
        lambda _path: {
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


def test_lookup_asset_total_adjusted_none_when_decimals_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns total_adjusted=None when the asset params don't include a decimals field."""
    monkeypatch.setattr(
        chain_tools,
        "_algod_get",
        lambda _path: {"params": {"total": 1000}},
    )
    result = chain_tools._tool_lookup_asset(1)
    assert result["total_adjusted"] is None


def test_get_asset_holder_share_computes_real_percentage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression-pin the actual CompX incident numbers: total=1e15 raw (decimals=6 -> 1 billion COMPX), creator holds 112,111,670,453,492 raw -> the real share is ~11.21%, NOT the "99.99%" the writer fabricated by doing the division itself and getting it wrong."""

    def fake_algod_get(path: str) -> dict:
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

    creator_addr = chain_tools._encode_address(b"\x02" * 32)
    result = chain_tools._tool_get_asset_holder_share(1732165149, creator_addr)
    assert result["total_supply_adjusted"] == 1_000_000_000.0
    assert result["holder_amount_adjusted"] == 112_111_670.453492
    assert result["share_pct"] == 11.2112
    assert result["share_pct"] != 99.99


def test_get_asset_holder_share_zero_when_address_does_not_hold_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns a 0.0 share_pct when the address holds none of the asset."""
    def fake_algod_get(path: str) -> dict:
        if path.startswith("/v2/assets/"):
            return {"params": {"total": 1000, "decimals": 0, "creator": "X"}}
        if path.startswith("/v2/accounts/"):
            return {"amount": 0, "status": "Offline", "assets": []}
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(chain_tools, "_algod_get", fake_algod_get)

    addr = chain_tools._encode_address(b"\x03" * 32)
    result = chain_tools._tool_get_asset_holder_share(1, addr)
    assert result["share_pct"] == 0.0


def test_get_asset_holder_share_requires_address() -> None:
    """Returns an error when no holder address is given."""
    result = chain_tools._tool_get_asset_holder_share(1, "")
    assert "error" in result


def test_get_asset_holder_share_propagates_asset_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Propagates an algod asset-lookup error (e.g. 404) as an error result."""
    monkeypatch.setattr(chain_tools, "_algod_get", lambda _path: {"_status": 404})
    result = chain_tools._tool_get_asset_holder_share(999, "ADDR")
    assert "error" in result


def test_asset_holder_share_tool_registered() -> None:
    """Registers get_asset_holder_share in both the tool schemas and handlers."""
    schemas, handlers = chain_tools.chain_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "get_asset_holder_share" in names
    assert "get_asset_holder_share" in handlers


def test_lookup_asset_by_name_returns_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Algod's lookup_asset needs a numeric id and can't search by name — this is the tool a stronger research model explicitly asked for (suggest_tool, 2026-07-14) when a project's asset_id guess 404'd. Uses the mainnet indexer, mirroring the existing testnet_lookup pattern."""
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None: {  # noqa: ARG005 -- name must match the real callee's keyword arg
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


def test_lookup_asset_by_name_finds_real_ticker_among_name_substring_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-caused 2026-07-16: searching "WAD" via the indexer's `name` param (old behavior) matched spam/airdrop tokens whose free-text NAME field happens to contain the substring "wad" (e.g. "32353024;WADIWYER") while completely missing the real token — DorkFi's actual stablecoin is named "Whale Asset Dollar" (ticker WAD), and "Whale Asset Dollar" does not contain the substring "wad" at all. A compose cited the spam asset's id as the real token's. The fix queries `unit` (the ticker field) instead and ranks an exact unit-name match first."""
    real = {
        "index": 3334160924,
        "params": {"name": "Whale Asset Dollar", "unit-name": "WAD", "creator": "REAL"},
    }
    partial = {
        "index": 292312665,
        "params": {"name": "Circumscribed", "unit-name": "cwad001", "creator": "OTHER"},
    }
    calls: list[dict] = []

    def fake_idx_get(_path: str, params: tuple | None = None) -> dict:
        calls.append(dict(params or {}))
        assert "unit" in params, "must search by ticker (unit), not free-text name"
        return {"assets": [partial, real]}

    monkeypatch.setattr(chain_tools, "_mainnet_idx_get", fake_idx_get)
    result = chain_tools._tool_lookup_asset_by_name("WAD", limit=5)
    assert len(calls) == 1  # exact ticker hit found — no name-search fallback needed
    assert result["results"][0]["asset_id"] == 3334160924
    assert result["results"][0]["unit_name"] == "WAD"


def test_lookup_asset_by_name_falls_back_to_display_name_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falls back to a display-name search when the ticker (unit) search finds no hits."""
    calls: list[dict] = []

    def fake_idx_get(_path: str, params: tuple | None = None) -> dict:
        calls.append(dict(params or {}))
        if "unit" in params:
            return {"assets": []}  # no ticker hits
        return {
            "assets": [
                {
                    "index": 1732165149,
                    "params": {"name": "CompX Token", "unit-name": "COMPX", "creator": "C"},
                }
            ]
        }

    monkeypatch.setattr(chain_tools, "_mainnet_idx_get", fake_idx_get)
    result = chain_tools._tool_lookup_asset_by_name("CompX")
    assert len(calls) == 2  # ticker search, then the display-name fallback
    assert result["results"][0]["asset_id"] == 1732165149


def test_lookup_asset_by_name_requires_nonempty_name() -> None:
    """An empty asset name returns an error instead of searching."""
    result = chain_tools._tool_lookup_asset_by_name("")
    assert "error" in result


def test_lookup_asset_by_name_propagates_indexer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An indexer error from the display-name fallback search is propagated to the caller."""
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None: {"error": "timeout"},  # noqa: ARG005 -- name must match the real callee's keyword arg
    )
    result = chain_tools._tool_lookup_asset_by_name("COMPX")
    assert result["error"] == "timeout"


def test_lookup_asset_by_name_tool_registered() -> None:
    """lookup_asset_by_name is registered as a callable chain tool."""
    schemas, handlers = chain_tools.chain_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "lookup_asset_by_name" in names
    assert "lookup_asset_by_name" in handlers


def test_is_valid_address_rejects_the_real_fabricated_addresses() -> None:
    """Regression-pin the actual 2026-07-14 incident: a model composing an NFT-marketplace article invented four plausible-looking addresses (each just the project's name as a prefix) and called lookup_account on them — all four failed algod with an unhelpful generic 400. Three of the four weren't even 58 characters."""
    for addr in (
        "EXA6RX5G6G2UXIMZ2HXV4C5OMJ3XKPN7VBP7GWQ2DEAF7WIOMCQBZWBXUY",
        "ALGOXNFT7KIZYGTA2T4T6336623FC6HDTYNY5YNVT4FHIMD2Q6UW73EDE",
        "DARTROOM5XUPXW7M7VKIGJ2T6H7WNWSJT27GXURCJN5XCQ5QJHQPHJQLQ2Y",
        "ABRIS5XUPXW7M7VKIGJ2T6H7WNWSJT27GXURCJN5XCQ5QJHQPHJQLQ2Y4",
    ):
        assert not chain_tools._is_valid_address(addr), addr


def test_is_valid_address_accepts_a_real_checksum() -> None:
    # _encode_address is the exact inverse of the check under test — a
    # genuine round-trip, not a hand-picked string that happens to look right.
    """Accepts a genuine checksum-valid address round-tripped through the encoder."""
    real = chain_tools._encode_address(b"\x00" * 32)
    assert len(real) == 58
    assert chain_tools._is_valid_address(real)


def test_lookup_account_rejects_invalid_address_without_hitting_algod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejects an invalid address before ever calling algod, so no network call is wasted on a fabricated string."""
    calls = []
    monkeypatch.setattr(chain_tools, "_algod_get", lambda path: calls.append(path) or {})

    fake_addr = "EXA6RX5G6G2UXIMZ2HXV4C5OMJ3XKPN7VBP7GWQ2DEAF7WIOMCQBZWBXUY"
    result = chain_tools._tool_lookup_account(fake_addr)

    assert "error" in result
    assert "never construct, guess" in result["error"]
    assert calls == []  # no wasted network call for a string that can't be real


def test_lookup_account_proceeds_for_a_valid_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proceeds to call algod and return the balance for a genuinely valid address."""
    real = chain_tools._encode_address(b"\x01" * 32)
    monkeypatch.setattr(
        chain_tools, "_algod_get", lambda _path: {"amount": 5_000_000, "assets": []}
    )

    result = chain_tools._tool_lookup_account(real)

    assert "error" not in result
    assert result["balance_algo"] == 5.0


def test_testnet_lookup_rejects_invalid_address_without_hitting_indexer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """testnet_lookup(address=...) had the SAME address-fabrication gap lookup_account did before it was fixed 2026-07-14 — found during a broader tool-degradation audit 2026-07-15 when a made-up test address triggered a generic 400 instead of a clear validation error."""
    calls = []
    monkeypatch.setattr(
        chain_tools,
        "_testnet_idx_get",
        lambda path, params=None: calls.append(path) or {},  # noqa: ARG005 -- name must match the real callee's keyword arg
    )

    fake_addr = "EXA6RX5G6G2UXIMZ2HXV4C5OMJ3XKPN7VBP7GWQ2DEAF7WIOMCQBZWBXUY"
    result = chain_tools._tool_testnet_lookup(address=fake_addr)

    assert "error" in result
    assert "never construct, guess" in result["error"]
    assert calls == []


def test_testnet_lookup_proceeds_for_a_valid_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """testnet_lookup proceeds to call the indexer for a genuinely valid address."""
    real = chain_tools._encode_address(b"\x04" * 32)
    monkeypatch.setattr(
        chain_tools,
        "_testnet_idx_get",
        lambda _path, params=None: {"account": {"amount": 1_000_000}},  # noqa: ARG005 -- name must match the real callee's keyword arg
    )

    result = chain_tools._tool_testnet_lookup(address=real)

    assert "error" not in result
    assert result["found"] is True
