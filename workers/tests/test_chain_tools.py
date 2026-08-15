"""On-chain lookup tools compute real percentages, never the writer's own arithmetic."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from conftest import FakeRedis

from app.modules.ai import chain_tools


def test_lookup_asset_computes_total_adjusted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw base-unit division must happen server-side, not be left to the model — this is exactly the arithmetic that went wrong in a real incident (2026-07-14): the writer manually converted a 15-digit raw ASA total and reported "1 trillion" for what should have been 1 billion."""
    monkeypatch.setattr(
        chain_tools,
        "_algod_get",
        lambda _path, **_kwargs: {
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
        lambda _path, **_kwargs: {"params": {"total": 1000}},
    )
    result = chain_tools._tool_lookup_asset(1)
    assert result["total_adjusted"] is None


def test_get_asset_holder_share_computes_real_percentage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression-pin the actual CompX incident numbers: total=1e15 raw (decimals=6 -> 1 billion COMPX), creator holds 112,111,670,453,492 raw -> the real share is ~11.21%, NOT the "99.99%" the writer fabricated by doing the division itself and getting it wrong."""

    def fake_algod_get(path: str, **_kwargs: object) -> dict:
        if path.startswith("/v2/accounts/") and "/assets/" in path:
            return {"asset-holding": {"amount": 112_111_670_453_492, "asset-id": 1732165149}}
        if path.startswith("/v2/assets/"):
            return {
                "params": {
                    "name": "CompX Token",
                    "total": 1_000_000_000_000_000,
                    "decimals": 6,
                    "creator": "CREATOR_ADDR",
                }
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

    def fake_algod_get(path: str, **_kwargs: object) -> dict:
        if path.startswith("/v2/accounts/") and "/assets/" in path:
            return {"_status": 404}
        if path.startswith("/v2/assets/"):
            return {"params": {"total": 1000, "decimals": 0, "creator": "X"}}
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
    monkeypatch.setattr(chain_tools, "_algod_get", lambda _path, **_kwargs: {"_status": 404})
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
        lambda _path, params=None, **_kwargs: {  # noqa: ARG005 -- name must match the real callee's keyword arg
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
            "total": None,
            "decimals": None,
            "total_adjusted": None,
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

    def fake_idx_get(_path: str, params: tuple | None = None, **_kwargs: object) -> dict:
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

    def fake_idx_get(_path: str, params: tuple | None = None, **_kwargs: object) -> dict:
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
        lambda _path, params=None, **_kwargs: {"error": "timeout"},  # noqa: ARG005 -- name must match the real callee's keyword arg
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
    monkeypatch.setattr(chain_tools, "_algod_get", lambda path, **_kwargs: calls.append(path) or {})

    fake_addr = "EXA6RX5G6G2UXIMZ2HXV4C5OMJ3XKPN7VBP7GWQ2DEAF7WIOMCQBZWBXUY"
    result = chain_tools._tool_lookup_account(fake_addr)

    assert "error" in result
    assert "never construct, guess" in result["error"]
    assert calls == []  # no wasted network call for a string that can't be real


def test_lookup_account_proceeds_for_a_valid_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proceeds to call algod and return the balance for a genuinely valid address."""
    real = chain_tools._encode_address(b"\x01" * 32)
    monkeypatch.setattr(
        chain_tools, "_algod_get", lambda _path, **_kwargs: {"amount": 5_000_000, "assets": []}
    )

    result = chain_tools._tool_lookup_account(real)

    assert "error" not in result
    assert result["balance_algo"] == 5.0


def test_lookup_account_paginates_created_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for a self-reported gap (2026-08-10).

    total_created_assets was always accurate, but the returned id list
    silently capped at 25 with no way to see the rest of a prolific
    creator's full roster (found on a 310-asset NFT series).
    """
    real = chain_tools._encode_address(b"\x02" * 32)
    created = [{"index": i} for i in range(60)]
    monkeypatch.setattr(
        chain_tools, "_algod_get", lambda _path, **_kwargs: {"amount": 0, "created-assets": created}
    )

    first = chain_tools._tool_lookup_account(real)
    assert first["total_created_assets"] == 60
    assert first["created_assets"] == list(range(25))
    assert first["created_assets_offset"] == 0
    assert first["created_assets_has_more"] is True

    second = chain_tools._tool_lookup_account(real, created_assets_offset=25)
    assert second["created_assets"] == list(range(25, 50))
    assert second["created_assets_has_more"] is True

    last = chain_tools._tool_lookup_account(real, created_assets_offset=50)
    assert last["created_assets"] == list(range(50, 60))
    assert last["created_assets_has_more"] is False


def test_testnet_lookup_rejects_invalid_address_without_hitting_indexer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """testnet_lookup(address=...) had the SAME address-fabrication gap lookup_account did before it was fixed 2026-07-14 — found during a broader tool-degradation audit 2026-07-15 when a made-up test address triggered a generic 400 instead of a clear validation error."""
    calls = []
    monkeypatch.setattr(
        chain_tools,
        "_testnet_idx_get",
        lambda path, params=None, **_kwargs: calls.append(path) or {},  # noqa: ARG005 -- name must match the real callee's keyword arg
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
        lambda _path, params=None, **_kwargs: {"account": {"amount": 1_000_000}},  # noqa: ARG005 -- name must match the real callee's keyword arg
    )

    result = chain_tools._tool_testnet_lookup(address=real)

    assert "error" not in result
    assert result["found"] is True


def test_round_to_date_rejects_both_or_neither_argument() -> None:
    """Exactly one of round/date is required — both or neither is a usage error."""
    assert "error" in chain_tools._tool_round_to_date()
    assert "error" in chain_tools._tool_round_to_date(round_number=5, date="2023-01-01")


def test_round_to_date_converts_a_round_to_its_block_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round -> its block's UTC timestamp, straight off the indexer."""
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None, **_kwargs: {"timestamp": 1_700_000_000},  # noqa: ARG005
    )

    result = chain_tools._tool_round_to_date(round_number=40_000_000)

    assert result["round"] == 40_000_000
    assert result["timestamp_utc"] == "2023-11-14T22:13:20+00:00"


def test_round_to_date_binary_searches_a_date_to_the_nearest_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake a linear round->timestamp timeline.

    Round 1..1000, 10s/round — confirms binary search converges on the exact
    round for a timestamp that lands on one.
    """
    base, step = 1_700_000_000, 10

    def fake_idx_get(path: str, params: dict | None = None, **_kwargs: object) -> dict:  # noqa: ARG001
        rnd = int(path.rsplit("/", 1)[1])
        return {"timestamp": base + rnd * step}

    monkeypatch.setattr(chain_tools, "_mainnet_idx_get", fake_idx_get)
    monkeypatch.setattr(chain_tools, "_algod_get", lambda _path, **_kwargs: {"last-round": 1000})

    target_ts = base + 500 * step
    target_date = datetime.fromtimestamp(target_ts, tz=UTC).isoformat()

    result = chain_tools._tool_round_to_date(date=target_date)

    assert result["nearest_round"] == 500


def test_round_to_date_clamps_a_date_before_genesis_to_round_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A date before round 1's timestamp clamps to round 1 instead of erroring."""
    base, step = 1_700_000_000, 10

    def fake_idx_get(path: str, params: dict | None = None, **_kwargs: object) -> dict:  # noqa: ARG001
        rnd = int(path.rsplit("/", 1)[1])
        return {"timestamp": base + rnd * step}

    monkeypatch.setattr(chain_tools, "_mainnet_idx_get", fake_idx_get)
    monkeypatch.setattr(chain_tools, "_algod_get", lambda _path, **_kwargs: {"last-round": 1000})

    result = chain_tools._tool_round_to_date(date="2000-01-01T00:00:00Z")

    assert result["nearest_round"] == 1
    assert "genesis" in result["note"]


def test_get_asset_transaction_volume_sums_a_single_complete_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A low-volume asset's whole history fits in one page — complete: true, exact totals."""
    txns = [
        {
            "id": f"TX{i}",
            "confirmed-round": 100 + i,
            "round-time": 1_700_000_000 + i,
            "tx-type": "axfer",
            "sender": "SENDER",
            "asset-transfer-transaction": {"amount": 10, "receiver": "RECEIVER"},
        }
        for i in range(5)
    ]
    # one opt-in (zero amount) mixed in — must not count toward real transfers
    txns.append(
        {
            "id": "TX-OPTIN",
            "confirmed-round": 200,
            "round-time": 1_700_000_200,
            "tx-type": "axfer",
            "sender": "SENDER",
            "asset-transfer-transaction": {"amount": 0, "receiver": "SENDER"},
        }
    )
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None, **_kwargs: {"transactions": txns},  # noqa: ARG005
    )

    result = chain_tools._tool_get_asset_transaction_volume(1732165149)

    assert result["complete"] is True
    assert result["transaction_count"] == 6
    assert result["real_transfer_count"] == 5
    assert result["total_amount_moved_raw"] == 50
    assert result["pages_fetched"] == 1


def test_get_asset_transaction_volume_reports_a_lower_bound_at_the_page_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A high-volume asset that never runs dry hits max_pages first — complete: false, and the totals are labeled as a lower bound, not the true lifetime figure."""

    def fake_idx_get(_path: str, params: dict | None = None, **_kwargs: object) -> dict:  # noqa: ARG001
        return {
            "transactions": [
                {
                    "id": "TX",
                    "confirmed-round": 500,
                    "round-time": 1_700_000_000,
                    "tx-type": "axfer",
                    "sender": "SENDER",
                    "asset-transfer-transaction": {"amount": 1, "receiver": "RECEIVER"},
                }
            ]
            * 1000,
            "next-token": "more",
        }

    monkeypatch.setattr(chain_tools, "_mainnet_idx_get", fake_idx_get)

    result = chain_tools._tool_get_asset_transaction_volume(1, max_pages=2)

    assert result["complete"] is False
    assert result["pages_fetched"] == 2
    assert result["transaction_count"] == 2000
    assert "LOWER BOUND" in result["note"]


def test_get_asset_transaction_volume_rejects_non_numeric_asset_id() -> None:
    """asset_id must be numeric — matches every other chain_tools handler's convention."""
    result = chain_tools._tool_get_asset_transaction_volume("not-a-number")
    assert "error" in result


def test_asset_transaction_volume_tool_registered() -> None:
    """Registers get_asset_transaction_volume in both the tool schemas and handlers."""
    schemas, handlers = chain_tools.chain_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "get_asset_transaction_volume" in names
    assert "get_asset_transaction_volume" in handlers


class _FakeResponse:
    """Minimal httpx.Response stand-in: status_code, .json(), and a raise_for_status that mimics real behavior for 4xx/5xx."""

    def __init__(self, json_body: dict, status_code: int = 200) -> None:
        self._json = json_body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._json


def _fake_client_factory(json_body: dict, status_code: int = 200) -> tuple[type, dict[str, int]]:
    """Build a fresh httpx.Client replacement plus a call counter, so each test gets its own isolated tally."""
    calls = {"n": 0}

    class _Client:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_a: object) -> bool:
            return False

        def get(self, _url: str, **_kwargs: object) -> _FakeResponse:
            calls["n"] += 1
            return _FakeResponse(json_body, status_code)

    return _Client, calls


class _BoomRedis:
    """A Redis stand-in whose every call raises, exercising the fail-soft path."""

    def get(self, _key: str) -> None:
        raise ConnectionError("redis down")

    def set(self, *_a: object, **_k: object) -> None:
        raise ConnectionError("redis down")


def test_algod_cache_hit_skips_second_http_call(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001 -- name must match the real callee's keyword arg
) -> None:
    """A second call with the same path and cache_ttl > 0 is served from cache, not a fresh HTTP round-trip."""
    client_cls, calls = _fake_client_factory({"amount": 1})
    monkeypatch.setattr(httpx, "Client", client_cls)

    first = chain_tools._algod_get("/v2/accounts/AAA", cache_ttl=60)
    second = chain_tools._algod_get("/v2/accounts/AAA", cache_ttl=60)

    assert first == {"amount": 1}
    assert second == {"amount": 1}
    assert calls["n"] == 1


def test_algod_cache_ttl_zero_never_caches(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001 -- name must match the real callee's keyword arg
) -> None:
    """The default cache_ttl=0 preserves the old always-fetch behavior."""
    client_cls, calls = _fake_client_factory({"amount": 1})
    monkeypatch.setattr(httpx, "Client", client_cls)

    chain_tools._algod_get("/v2/accounts/AAA")
    chain_tools._algod_get("/v2/accounts/AAA")

    assert calls["n"] == 2


def test_algod_error_response_never_cached(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001 -- name must match the real callee's keyword arg
) -> None:
    """A 500 (surfaced as {"error": ...}) is never stored, so every call re-hits algod."""
    client_cls, calls = _fake_client_factory({}, status_code=500)
    monkeypatch.setattr(httpx, "Client", client_cls)

    first = chain_tools._algod_get("/v2/accounts/AAA", cache_ttl=60)
    second = chain_tools._algod_get("/v2/accounts/AAA", cache_ttl=60)

    assert "error" in first
    assert "error" in second
    assert calls["n"] == 2


def test_algod_404_never_cached(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001 -- name must match the real callee's keyword arg
) -> None:
    """A missing entity ({"_status": 404}) is never stored either, so a not-yet-confirmed asset re-checks each time."""
    client_cls, calls = _fake_client_factory({}, status_code=404)
    monkeypatch.setattr(httpx, "Client", client_cls)

    first = chain_tools._algod_get("/v2/assets/999999", cache_ttl=60)
    second = chain_tools._algod_get("/v2/assets/999999", cache_ttl=60)

    assert first == {"_status": 404}
    assert second == {"_status": 404}
    assert calls["n"] == 2


def test_algod_redis_failure_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken Redis (get and set both raising) still lets the tool return the real result -- fail-soft, never a hard error."""
    monkeypatch.setattr(chain_tools, "_redis_client", lambda: _BoomRedis())
    client_cls, calls = _fake_client_factory({"amount": 5})
    monkeypatch.setattr(httpx, "Client", client_cls)

    result = chain_tools._algod_get("/v2/accounts/AAA", cache_ttl=60)

    assert result == {"amount": 5}
    assert calls["n"] == 1


def test_nft_collection_distribution_timeline_uses_asset_ids_when_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asset_ids bypasses the created-assets sampling entirely -- checks exactly the given ids, no algod account call at all."""
    creator = chain_tools._encode_address(b"\x04" * 32)
    buyer = chain_tools._encode_address(b"\x05" * 32)

    def fake_algod_get(_path: str, **_kwargs: object) -> dict:
        raise AssertionError("must not call algod when asset_ids is given")

    def fake_idx_get(path: str, params: dict | None = None, **_kwargs: object) -> dict:  # noqa: ARG001
        return {
            "transactions": [
                {
                    "id": "TX1",
                    "confirmed-round": 100,
                    "round-time": 1_700_000_000,
                    "tx-type": "axfer",
                    "sender": creator,
                    "asset-transfer-transaction": {"amount": 1, "receiver": buyer},
                }
            ]
        }

    monkeypatch.setattr(chain_tools, "_algod_get", fake_algod_get)
    monkeypatch.setattr(chain_tools, "_mainnet_idx_get", fake_idx_get)

    result = chain_tools._tool_nft_collection_distribution_timeline(
        creator, asset_ids=[111, 222]
    )

    assert result["sampled"] == 2
    assert result["total_created_assets"] == 2
    assert result["claimed_count"] == 2
    assert all(item["claimed"] for item in result["items"])
    assert result["items"][0]["claimed_by"] == buyer


def test_nft_collection_distribution_timeline_flags_unclaimed_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An asset with no real transfer FROM the creator (still held, or only opt-in noise) is reported claimed: false, not silently omitted."""
    creator = chain_tools._encode_address(b"\x06" * 32)
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None, **_kwargs: {"transactions": []},  # noqa: ARG005
    )

    result = chain_tools._tool_nft_collection_distribution_timeline(creator, asset_ids=[999])

    assert result["claimed_count"] == 0
    assert result["unclaimed_count"] == 1
    assert result["items"][0]["claimed"] is False


def test_nft_collection_distribution_timeline_flags_a_later_resale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real transfer AFTER the creator's initial send, from a non-creator sender, marks resold_since -- a signal this tool deliberately doesn't price, since that would require a different investigation."""
    creator = chain_tools._encode_address(b"\x07" * 32)
    buyer1 = chain_tools._encode_address(b"\x08" * 32)
    buyer2 = chain_tools._encode_address(b"\x09" * 32)

    def fake_idx_get(_path: str, params: dict | None = None, **_kwargs: object) -> dict:  # noqa: ARG001
        return {
            "transactions": [
                {
                    "id": "TX1",
                    "confirmed-round": 100,
                    "round-time": 1_700_000_000,
                    "tx-type": "axfer",
                    "sender": creator,
                    "asset-transfer-transaction": {"amount": 1, "receiver": buyer1},
                },
                {
                    "id": "TX2",
                    "confirmed-round": 200,
                    "round-time": 1_700_000_500,
                    "tx-type": "axfer",
                    "sender": buyer1,
                    "asset-transfer-transaction": {"amount": 1, "receiver": buyer2},
                },
            ]
        }

    monkeypatch.setattr(chain_tools, "_mainnet_idx_get", fake_idx_get)

    result = chain_tools._tool_nft_collection_distribution_timeline(creator, asset_ids=[1])

    assert result["items"][0]["resold_since"] is True
    assert result["resold_count"] == 1
    # claimed_by is still the FIRST (creator-originated) recipient, not the reseller's buyer.
    assert result["items"][0]["claimed_by"] == buyer1


def test_nft_collection_distribution_timeline_samples_from_created_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No asset_ids given -- samples up to max_assets from the creator's own created-assets list (oldest-first), and reports the true total alongside the sample size."""
    creator = chain_tools._encode_address(b"\x0a" * 32)
    created = [{"index": 1000 + i} for i in range(10)]
    monkeypatch.setattr(
        chain_tools,
        "_algod_get",
        lambda _path, **_kwargs: {"created-assets": created},
    )
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None, **_kwargs: {"transactions": []},  # noqa: ARG005
    )

    result = chain_tools._tool_nft_collection_distribution_timeline(creator, max_assets=3)

    assert result["total_created_assets"] == 10
    assert result["sampled"] == 3
    assert "sampled 3 of 10" in result["note"]


def test_nft_collection_distribution_timeline_requires_a_valid_address() -> None:
    """A malformed creator_address is rejected before any network call, same as every other chain tool."""
    result = chain_tools._tool_nft_collection_distribution_timeline("not-a-real-address")
    assert "error" in result


def test_cache_key_is_order_independent_for_params() -> None:
    """Two logically-identical param dicts in different insertion order produce the same cache key."""
    key_a = chain_tools._cache_key("mainnet_idx", "/v2/assets", {"name": "foo", "limit": 5})
    key_b = chain_tools._cache_key("mainnet_idx", "/v2/assets", {"limit": 5, "name": "foo"})
    assert key_a == key_b


def test_mainnet_idx_cache_hit_with_reordered_params_skips_second_http_call(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001 -- name must match the real callee's keyword arg
) -> None:
    """The same call made with params in a different dict order still hits the same cache entry, not a fresh HTTP call."""
    client_cls, calls = _fake_client_factory({"assets": []})
    monkeypatch.setattr(httpx, "Client", client_cls)

    chain_tools._mainnet_idx_get("/v2/assets", {"name": "foo", "limit": 5}, cache_ttl=60)
    chain_tools._mainnet_idx_get("/v2/assets", {"limit": 5, "name": "foo"}, cache_ttl=60)

    assert calls["n"] == 1
