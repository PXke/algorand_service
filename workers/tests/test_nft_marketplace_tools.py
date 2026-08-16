"""NFT marketplace lookups parse rendered SPA text, not a real API — pin real page-shape samples."""

from __future__ import annotations

import pytest

from app.modules.ai import nft_marketplace_tools as nft


def test_parse_amount_handles_suffixes_and_dashes() -> None:
    """Suffix/comma/dash parsing for the raw text amounts every marketplace renders."""
    assert nft._parse_amount("25") == 25.0
    assert nft._parse_amount("2.7K") == 2700.0
    assert nft._parse_amount("1,234.5") == 1234.5
    assert nft._parse_amount("--") is None
    assert nft._parse_amount("") is None


def test_slugify_matches_observed_downbad_and_exa_convention() -> None:
    """Slug derivation matches the real convention observed live on Downbad and Exa Market."""
    assert nft._slugify("Al Goanna") == "al-goanna"
    assert nft._slugify("Pixel City") == "pixel-city"
    assert nft._slugify("  Weird!!  Punctuation??") == "weird-punctuation"


# --- per-asset listing status --------------------------------------------


def test_statto_asset_listing_parses_a_for_sale_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real page shape captured live 2026-08-10 (statto.xyz/assets/1044044405)."""
    sample = (
        "Details\nOffers\nSales (2)\nLending (0)\nHaramboi #165\nHaramboiz\n"
        "#1044044405\nRank 1,413 of 2,221\nARC69\nCREATED\nFeb 22, 2023\n"
        "CREATOR\nHRMJ...MBOI\nHOLDER\nNTMK...MOKI\nFOR SALE\nListed by\n"
        "rich.staci\n45.00\nMake offer\n"
    )
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: sample)
    result = nft._statto_asset_listing("1044044405")
    assert result["listed"] is True
    assert result["listed_by"] == "rich.staci"
    assert result["price_algo"] == 45.0


def test_statto_asset_listing_not_for_sale(monkeypatch: pytest.MonkeyPatch) -> None:
    """No FOR SALE marker in the rendered text means not listed."""
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: "Haramboi #33\nCREATOR\nHOLDER\nTRAITS\n")
    result = nft._statto_asset_listing("1044032285")
    assert result["listed"] is False


def test_statto_asset_listing_propagates_render_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed render surfaces as an error, not a false 'not listed'."""
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: None)
    result = nft._statto_asset_listing("1")
    assert "error" in result


def test_randgallery_asset_listing_parses_current_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real page shape captured live 2026-08-10 (randgallery.com/detail/1044032285)."""
    sample = "Haramboi #33\nOwner:\nAPVAC...SHWNE\n0 Offers\nCurrent Price\n37.00\n( $2.96 )\nBuy\n"
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: sample)
    result = nft._randgallery_asset_listing("1044032285")
    assert result["listed"] is True
    assert result["price_algo"] == 37.0


def test_randgallery_asset_listing_not_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Current Price marker means not listed."""
    sample = "Haramboi #165\nHBOI0165\n1044044405\nAttributes\nRank\n1414\n"
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: sample)
    result = nft._randgallery_asset_listing("1044044405")
    assert result["listed"] is False


def test_exa_asset_listing_true_when_buy_now_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Buy now' text on the card means listed."""
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: "DonkeyDAO\nDonkey 2031\nBuy now\nPrice\n350\n")
    result = nft._exa_asset_listing("1")
    assert result["listed"] is True


def test_exa_asset_listing_false_when_only_make_offer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real page shape captured live 2026-08-10 (exa.market/asset/1044044405, not listed)."""
    sample = "Haramboiz\nHaramboi #165\n1416 / 2222\nOwner\nntmkmoki\nMake offer\nTraits\n"
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: sample)
    result = nft._exa_asset_listing("1044044405")
    assert result["listed"] is False


def test_nft_asset_listing_status_rejects_non_numeric_id() -> None:
    """asset_id must be numeric — matches chain_tools' convention."""
    result = nft._tool_nft_asset_listing_status("not-a-number")
    assert "error" in result


def test_nft_asset_listing_status_aggregates_all_four_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Combines all four per-asset sources into one result, each independently."""
    monkeypatch.setattr(nft, "_downbad_asset_listing", lambda _aid, *_a: {"listed": True, "price_algo": 60.0})
    monkeypatch.setattr(nft, "_statto_asset_listing", lambda _aid, *_a: {"listed": True})
    monkeypatch.setattr(nft, "_randgallery_asset_listing", lambda _aid, *_a: {"listed": False})
    monkeypatch.setattr(nft, "_exa_asset_listing", lambda _aid, *_a: {"error": "could not render"})

    result = nft._tool_nft_asset_listing_status(1044044405)

    assert result["asset_id"] == 1044044405
    assert result["downbad"]["listed"] is True
    assert result["statto"]["listed"] is True
    assert result["rand_gallery"]["listed"] is False
    assert "error" in result["exa_market"]


def test_downbad_asset_listing_parses_a_real_page_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real page shape captured live 2026-08-11 (downbad.farm/asset/3637324709, listed at 60A)."""
    sample = "Item\nOffers\nDESCRIPTION\nYour key to the dungeon\nListed on downbad\n60\nBUY NOW\nMAKE OFFER\n"
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: sample)
    result = nft._downbad_asset_listing("3637324709")
    assert result["listed"] is True
    assert result["price_algo"] == 60.0


def test_downbad_asset_listing_not_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Not Listed' text on the item page means not listed."""
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: "Item\nLumi Ankh 1\nNot Listed\nMake offer\n")
    result = nft._downbad_asset_listing("3637324709")
    assert result["listed"] is False


def test_downbad_asset_listing_propagates_render_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed render surfaces as an error, not a false 'not listed'."""
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: None)
    result = nft._downbad_asset_listing("1")
    assert "error" in result


# --- per-collection market stats -------------------------------------------


def test_downbad_collection_stats_parses_a_real_page_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real page shape captured live 2026-08-10 (downbad.farm/collection/pixel-city)."""
    sample = (
        "Pixel City\nBY PIXEL CITY\n246 ITEMS\nCREATED JUN 2026\nFloor25A\n"
        "Listed6 (2.4%)\nTotal Vol2.7KA\nOwners76 (31%)\nItems\n"
    )
    # No real slug-discovery network attempt in this unit test -- isolate it,
    # same as _render below, rather than let it fall through to a real
    # (doomed, no-network-guard-blocked) browser launch attempt.
    monkeypatch.setattr(nft, "_downbad_discover_slug", lambda _name, *_a: None)
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: sample)
    result = nft._downbad_collection_stats("Pixel City")
    assert result["found"] is True
    assert result["tried_slug"] == "pixel-city"
    assert result["items"] == 246
    assert result["floor_algo"] == 25.0
    assert result["listed_count"] == 6
    assert result["listed_pct"] == 2.4
    assert result["total_volume_algo"] == 2700.0
    assert result["owner_count"] == 76
    assert result["owner_pct"] == 31.0


def test_downbad_collection_stats_reports_not_found_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client-side 404 (still HTTP 200) is detected from the rendered text and reported as not found."""
    monkeypatch.setattr(nft, "_downbad_discover_slug", lambda _name, *_a: None)
    monkeypatch.setattr(
        nft, "_render", lambda _url, *_a: "404\nThis page could not be found."
    )
    result = nft._downbad_collection_stats("Nonexistent Collection")
    assert result["found"] is False


def test_downbad_collection_stats_uses_discovered_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root-caused 2026-08-11: 'Lumi Rogue'/'Ankh' naive-slugify to URLs that don't exist -- the real Downbad slug is 'lumi-rogue-ankhs', only findable by discovery. When discovery succeeds, its slug wins over the naive guess."""
    monkeypatch.setattr(nft, "_downbad_discover_slug", lambda _name, *_a: "lumi-rogue-ankhs")
    seen_urls = []

    def fake_render(url: str, *_a: object) -> str:
        seen_urls.append(url)
        return "Lumi Rogue Ankhs\nBY LUMI ROGUE ANKHS\n1000 ITEMS\nFloor60A\nListed4 (0.4%)\nTotal Vol556.9A\nOwners36 (4%)\n"

    monkeypatch.setattr(nft, "_render", fake_render)
    result = nft._downbad_collection_stats("Ankh")
    assert result["tried_slug"] == "lumi-rogue-ankhs"
    assert result["floor_algo"] == 60.0
    # Also renders the shuffle tab, same discovered slug -- see the shuffle-specific tests below.
    assert seen_urls == [
        "https://www.downbad.farm/collection/lumi-rogue-ankhs",
        "https://www.downbad.farm/collection/lumi-rogue-ankhs?tab=shuffle",
    ]


def test_downbad_collection_stats_reports_not_found_when_page_matches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rendered page that matches none of the stat patterns (generic app shell from a wrong/stale slug) is reported as not found, not as found:true with a wall of nulls."""
    monkeypatch.setattr(nft, "_downbad_discover_slug", lambda _name, *_a: None)
    monkeypatch.setattr(
        nft,
        "_render",
        lambda _url, *_a: "Discover\nCollections\nTokens\nAuctions\nDeals\nOffers\nLending\n",
    )
    result = nft._downbad_collection_stats("Some Collection")
    assert result["found"] is False


def test_downbad_shuffle_status_parses_a_real_page_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real shuffle-tab shape captured live 2026-08-16 (downbad.farm/collection/lumi-rogue-ankhs?tab=shuffle) -- the mystery-box pool root-caused as the explanation for a creator-controlled wallet holding 970/1000 of the collection."""
    sample = (
        "Lumi rogue ankhs\nwelcome to the Amduat\n69\nAVAILABLE NOW\nConnect\n"
        "959/970 remaining\nShuffle Schedule\nwelcome to the Amduat\n"
        "Starts: 7/14/2026, 12:00:00 AM\n69\n"
    )
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: sample)
    result = nft._downbad_shuffle_status("lumi-rogue-ankhs")
    assert result["active"] is True
    assert result["price_algo"] == 69.0
    assert result["remaining"] == 959
    assert result["pool_size"] == 970
    assert result["starts"] == "7/14/2026, 12:00:00 AM"


def test_downbad_shuffle_status_inactive_when_no_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """The common case: a collection with no shuffle feature at all -- reported as active: False, not an error."""
    monkeypatch.setattr(
        nft, "_render", lambda _url, *_a: "Pixel City\nBY PIXEL CITY\n246 ITEMS\nFloor25A\nItems\n"
    )
    result = nft._downbad_shuffle_status("pixel-city")
    assert result == {"active": False}


def test_downbad_shuffle_status_propagates_render_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed render is reported as an error, not silently treated as active: False."""
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: None)
    result = nft._downbad_shuffle_status("lumi-rogue-ankhs")
    assert "error" in result


def test_downbad_collection_stats_includes_inactive_shuffle_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """test_downbad_collection_stats_parses_a_real_page_shape's sample has no shuffle markers -- shuffle.active must be False, not missing."""
    sample = (
        "Pixel City\nBY PIXEL CITY\n246 ITEMS\nCREATED JUN 2026\nFloor25A\n"
        "Listed6 (2.4%)\nTotal Vol2.7KA\nOwners76 (31%)\nItems\n"
    )
    monkeypatch.setattr(nft, "_downbad_discover_slug", lambda _name, *_a: None)
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: sample)
    result = nft._downbad_collection_stats("Pixel City")
    assert result["shuffle"] == {"active": False}


def test_downbad_discover_slug_matches_by_substring_not_exact_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'Ankh' must match slug word 'ankhs' (plural) via substring, not exact equality."""
    html = '<a href="/collection/lumi-rogue-ankhs">Lumi Rogue Ankhs</a><a href="/collection/al-goanna">Al Goanna</a>'
    monkeypatch.setattr(nft, "_render_html", lambda _url, *_a: html)
    assert nft._downbad_discover_slug("Ankh") == "lumi-rogue-ankhs"
    assert nft._downbad_discover_slug("Lumi Rogue") == "lumi-rogue-ankhs"
    assert nft._downbad_discover_slug("Al Goanna") == "al-goanna"


def test_downbad_discover_slug_returns_none_when_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """No candidate shares any word with the query -- returns None so the caller falls back to the naive slug guess."""
    html = '<a href="/collection/al-goanna">Al Goanna</a>'
    monkeypatch.setattr(nft, "_render_html", lambda _url, *_a: html)
    assert nft._downbad_discover_slug("Completely Unrelated Project") is None


def test_downbad_discover_slug_returns_none_on_render_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed homepage render degrades to None, never raises."""
    monkeypatch.setattr(nft, "_render_html", lambda _url, *_a: None)
    assert nft._downbad_discover_slug("Ankh") is None


def test_randgallery_collection_stats_parses_a_real_page_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real page shape captured live 2026-08-10 (randgallery.com/collections/Haramboiz)."""
    sample = "Haramboiz\nFloor\n37.00\n24h Sales\n25.00\nHighest Sale\n250.00\nListings\n118\nNfts\n"
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: sample)
    result = nft._randgallery_collection_stats("Haramboiz")
    assert result["found"] is True
    assert result["floor_algo"] == 37.0
    assert result["highest_sale_algo"] == 250.0
    assert result["listed_count"] == 118


def test_randgallery_collection_stats_not_found_when_no_markers_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No floor/highest/listings markers at all means the collection page didn't resolve."""
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: "Nothing here\n")
    result = nft._randgallery_collection_stats("Nonexistent")
    assert result["found"] is False


def test_exa_collection_stats_parses_a_real_page_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real page shape captured live 2026-08-10 (exa.market/collection/algobots)."""
    sample = (
        "Algobots\nBy\nbankon.algo\nRoyalties 5%\nTotal volume\n2.05k\n"
        "Floor price\n--\nHighest sale\n500\nListed\n--\nItems\n393\nActivity\n"
    )
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: sample)
    result = nft._exa_collection_stats("Algobots")
    assert result["found"] is True
    assert result["total_volume_algo"] == 2050.0
    assert result["floor_algo"] is None
    assert result["highest_sale_algo"] == 500.0
    assert result["items"] == 393


def test_exa_collection_stats_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """No 'Total volume' marker means the slug guess missed."""
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: "Sign In\nGet Started\n")
    result = nft._exa_collection_stats("Nonexistent")
    assert result["found"] is False


def test_statto_collection_stats_finds_a_row_in_the_top_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name match in the homepage's ranked table is reported as found."""
    sample = "Top Collections by Volume\nHaramboiz\nby Algorillas\n58.00\nNew\n—\n35.00\n—\n32\n3\n19.33\n"
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: sample)
    result = nft._statto_collection_stats("Haramboiz")
    assert result["found_in_top_collections_table"] is True


def test_statto_collection_stats_not_found_is_not_a_negative_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not appearing in the visible ranked table is explicitly NOT the same as not existing on Statto."""
    monkeypatch.setattr(nft, "_render", lambda _url, *_a: "Top Collections by Volume\nOther Thing\n")
    result = nft._statto_collection_stats("Some Niche Collection")
    assert result["found_in_top_collections_table"] is False
    assert "does NOT mean" in result["note"]


def test_nft_collection_market_stats_requires_a_name() -> None:
    """An empty collection name is a usage error, not a search."""
    result = nft._tool_nft_collection_market_stats("")
    assert "error" in result


def test_nft_collection_market_stats_aggregates_all_four_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Combines all four per-collection sources into one result, each independently."""
    monkeypatch.setattr(nft, "_downbad_collection_stats", lambda _name, *_a: {"found": True})
    monkeypatch.setattr(nft, "_randgallery_collection_stats", lambda _name, *_a: {"found": False})
    monkeypatch.setattr(nft, "_exa_collection_stats", lambda _name, *_a: {"found": True})
    monkeypatch.setattr(
        nft, "_statto_collection_stats", lambda _name, *_a: {"found_in_top_collections_table": False}
    )

    result = nft._tool_nft_collection_market_stats("Pixel City")

    assert result["collection"] == "Pixel City"
    assert result["downbad"]["found"] is True
    assert result["rand_gallery"]["found"] is False
    assert result["exa_market"]["found"] is True
    assert result["statto"]["found_in_top_collections_table"] is False


def test_nft_marketplace_tools_registers_both_tools() -> None:
    """Registers both tools in the schemas and handlers returned by nft_marketplace_tools()."""
    schemas, handlers = nft.nft_marketplace_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "nft_asset_listing_status" in names
    assert "nft_collection_market_stats" in names
    assert "nft_asset_listing_status" in handlers
    assert "nft_collection_market_stats" in handlers


def test_render_reuses_a_given_playwright_session_instead_of_a_one_off_browser() -> None:
    """Root-caused 2026-08-11 (writer complaint: nft_collection_market_stats couldn't render ANY of 4 marketplaces): every render here used to launch its own standalone browser via fetch_page. When a session is given, _render must use it instead of importing fetch_page at all."""
    import unittest.mock

    session = unittest.mock.MagicMock()
    session.fetch.return_value = unittest.mock.MagicMock(text="rendered via session")

    result = nft._render("https://example.com", session)

    assert result == "rendered via session"
    session.fetch.assert_called_once_with("https://example.com", skip_login_wall_check=True)


def test_render_falls_back_to_a_one_off_browser_without_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No session given (e.g. called outside a compose) -- falls back to the old one-off fetch_page path."""
    import unittest.mock

    fake_result = unittest.mock.MagicMock(text="rendered via one-off fetch_page")
    monkeypatch.setattr(
        "app.modules.scraper.core.browser_scrape.fetch_page", lambda *a, **kw: fake_result  # noqa: ARG005
    )
    result = nft._render("https://example.com")
    assert result == "rendered via one-off fetch_page"
