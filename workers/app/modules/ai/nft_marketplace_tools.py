"""Algorand NFT marketplace lookups, via Playwright rendering, not an API.

None of the marketplaces named in self-reported tool gaps (Rand Gallery,
Downbad, Statto, Exa Market) expose a documented public API (confirmed
2026-08-10; a 4th, ALGOxNFT, shut down entirely in mid-2025). Every marketplace
here is a client-side-rendered SPA, so each lookup goes through
browser_scrape.fetch_page and parses the rendered text -- inherently more
fragile than an API wrapper (a site redesign breaks the parser, not just a
field name), so every handler is failure-tolerant per-source: one
marketplace's parse failure never blocks the others', and the caller always
gets back whichever sources DID resolve plus an explicit error for the ones
that didn't.

skip_login_wall_check=True is used throughout: these are pre-vetted public
marketplace pages whose "Sign In" nav chrome otherwise false-positives
browser_scrape's login-wall heuristic (confirmed live on exa.market).

playwright_session (2026-08-11, root-caused live: a Lumi Rogue Ankh
collection lookup got "could not render" from all FOUR marketplaces at
once): every render here used to launch its own standalone Chromium via
browser_scrape.fetch_page -- up to 8 separate browser launches for one
nft_collection_market_stats call. That's exactly the load pattern that made
"always render via Playwright" only affordable elsewhere by reusing ONE
browser per compose (see maybe_start_session in browser_scrape.py); this
module predates that fix and was never updated to use it. Every render
function here now accepts an optional playwright_session and reuses it when
given, injected from compose context the same way as click_element/
type_into_page (see writer_tools._wrap_browser_action) -- falls back to a
one-off browser when no session is available (e.g. called outside a
compose).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_WAIT_MS = 5000


def _render(url: str, playwright_session: Any = None) -> str | None:  # noqa: ANN401 -- PlaywrightSession
    """Best-effort Playwright fetch of one marketplace page's visible text. None on any failure."""
    try:
        if playwright_session is not None:
            return playwright_session.fetch(url, skip_login_wall_check=True).text
        from app.modules.scraper.core.browser_scrape import fetch_page

        return fetch_page(url, wait_after_load_ms=_WAIT_MS, skip_login_wall_check=True).text
    except Exception as exc:
        logger.debug("nft marketplace render failed for %s: %s", url, exc)
        return None


def _render_html(url: str, playwright_session: Any = None) -> str | None:  # noqa: ANN401 -- PlaywrightSession
    """Same as _render, but the raw rendered HTML (for extracting real <a href> links) instead of visible text. None on any failure."""
    try:
        if playwright_session is not None:
            return playwright_session.fetch(url, skip_login_wall_check=True).html
        from app.modules.scraper.core.browser_scrape import fetch_page

        return fetch_page(url, wait_after_load_ms=_WAIT_MS, skip_login_wall_check=True).html
    except Exception as exc:
        logger.debug("nft marketplace html render failed for %s: %s", url, exc)
        return None


_DOWNBAD_COLLECTION_HREF_RE = re.compile(r'href="/collection/([a-z0-9-]+)"')


def _downbad_discover_slug(name: str, playwright_session: Any = None) -> str | None:  # noqa: ANN401 -- PlaywrightSession
    """The real Downbad collection slug for `name`, discovered from the live homepage's actual collection links rather than guessed by slugifying the display name.

    Root-caused 2026-08-11 (owner: bought a Lumi Rogue Ankh on Downbad,
    asked why the tool couldn't find it): naive slugify('Lumi Rogue') ->
    'lumi-rogue' and slugify('Ankh') -> 'ankh', but the real collection is
    listed under 'lumi-rogue-ankhs' -- neither guess matches, so every
    query against it silently rendered an empty not-found-shaped page
    (found: true, every field null) instead of real data.

    Matches on substring containment, not exact token equality (so 'Ankh'
    still matches the slug word 'ankhs' despite the plural), scored by how
    many significant words of `name` appear in a candidate slug. None if
    nothing scores -- caller falls back to the naive slug guess, which
    still works fine for the common case where display name and slug
    genuinely agree.
    """
    html = _render_html("https://downbad.farm/", playwright_session)
    if html is None:
        return None
    candidates = set(_DOWNBAD_COLLECTION_HREF_RE.findall(html))
    words = [w for w in re.split(r"[^a-z0-9]+", name.strip().lower()) if len(w) > 2]
    if not words or not candidates:
        return None
    best_slug: str | None = None
    best_score = 0
    for slug in candidates:
        slug_words = slug.split("-")
        score = sum(1 for w in words if any(w in sw for sw in slug_words))
        if score > best_score:
            best_slug, best_score = slug, score
    return best_slug


def _slugify(name: str) -> str:
    """Lowercase, spaces/punctuation -> single hyphens -- matches the slug convention observed live on both Downbad and Exa Market (e.g. 'Al Goanna' -> 'al-goanna')."""
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    return s.strip("-")


_NUM_RE = re.compile(r"-?[\d,]+\.?\d*[kKmM]?")


def _parse_amount(text: str) -> float | None:
    """'2.7K' -> 2700.0, '1,234.5' -> 1234.5, '--'/'' -> None."""
    t = text.strip().replace(",", "")
    if not t or t == "--":
        return None
    mult = 1.0
    if t[-1] in "kK":
        mult, t = 1_000.0, t[:-1]
    elif t[-1] in "mM":
        mult, t = 1_000_000.0, t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Per-asset listing status
# ---------------------------------------------------------------------------

_STATTO_FOR_SALE_RE = re.compile(
    r"FOR SALE\s*\nListed by\s*\n([^\n]+)\n([\d,]+\.?\d*)", re.I
)
_RAND_CURRENT_PRICE_RE = re.compile(r"Current Price\s*\n([\d,]+\.?\d*)")
_DOWNBAD_LISTED_ON_RE = re.compile(r"Listed on downbad\s*\n?([\d,.]+[kKmM]?)", re.I)


def _statto_asset_listing(asset_id: str, playwright_session: Any = None) -> dict[str, Any]:  # noqa: ANN401
    text = _render(f"https://www.statto.xyz/assets/{asset_id}", playwright_session)
    if text is None:
        return {"error": "could not render statto.xyz asset page"}
    if "FOR SALE" not in text:
        return {"listed": False}
    m = _STATTO_FOR_SALE_RE.search(text)
    if not m:
        return {"listed": True, "note": "shows FOR SALE but price could not be parsed"}
    return {"listed": True, "listed_by": m.group(1).strip(), "price_algo": _parse_amount(m.group(2))}


def _randgallery_asset_listing(asset_id: str, playwright_session: Any = None) -> dict[str, Any]:  # noqa: ANN401
    text = _render(f"https://www.randgallery.com/detail/{asset_id}", playwright_session)
    if text is None:
        return {"error": "could not render randgallery.com asset page"}
    m = _RAND_CURRENT_PRICE_RE.search(text)
    if not m:
        return {"listed": False}
    return {"listed": True, "price_algo": _parse_amount(m.group(1))}


def _exa_asset_listing(asset_id: str, playwright_session: Any = None) -> dict[str, Any]:  # noqa: ANN401
    text = _render(f"https://exa.market/asset/{asset_id}", playwright_session)
    if text is None:
        return {"error": "could not render exa.market asset page"}
    # Exa shows "Buy now" ahead of price on a listed item's own detail page,
    # and only "Make offer" (no "Buy now") when it isn't for sale -- confirmed
    # against a known-listed and a known-unlisted asset live 2026-08-10.
    return {"listed": "buy now" in text.lower()}


def _downbad_asset_listing(asset_id: str, playwright_session: Any = None) -> dict[str, Any]:  # noqa: ANN401
    """Whether a specific asset is listed on Downbad, via its direct /asset/<id> page.

    Root-caused 2026-08-11: the writer itself discovered this URL works
    (https://www.downbad.farm/asset/<id>) while working around a
    nft_collection_market_stats render failure -- this docstring and
    nft_asset_listing_status's used to claim Downbad has no per-asset URL
    at all, based on an earlier click-based exploration that only found the
    in-page modal path. Direct navigation to the same URL works fine.
    """
    text = _render(f"https://www.downbad.farm/asset/{asset_id}", playwright_session)
    if text is None:
        return {"error": "could not render downbad.farm asset page"}
    if "not listed" in text.lower():
        return {"listed": False}
    m = _DOWNBAD_LISTED_ON_RE.search(text)
    if m:
        return {"listed": True, "price_algo": _parse_amount(m.group(1))}
    if "buy now" in text.lower():
        return {"listed": True, "note": "shows BUY NOW but price could not be parsed"}
    return {"listed": False}


def _tool_nft_asset_listing_status(
    asset_id: int | str,
    playwright_session: Any = None,  # noqa: ANN401 -- PlaywrightSession; injected from compose context, see writer_tools._wrap_browser_action
) -> dict[str, Any]:
    """Whether a specific Algorand NFT (by ASA id) is CURRENTLY listed for sale, and at what price, across Downbad, Statto, Rand Gallery, and Exa Market -- and on which marketplace, since an item can be listed on one and not another (confirmed live: a Haramboiz piece was listed on Rand Gallery but showed only 'Make offer', not listed, on Exa Market at the same time). Slow (multiple page renders) -- expect several seconds."""
    aid = str(asset_id).strip()
    if not aid.isdigit():
        return {"error": "asset_id must be numeric"}
    return {
        "asset_id": int(aid),
        "downbad": _downbad_asset_listing(aid, playwright_session),
        "statto": _statto_asset_listing(aid, playwright_session),
        "rand_gallery": _randgallery_asset_listing(aid, playwright_session),
        "exa_market": _exa_asset_listing(aid, playwright_session),
    }


# ---------------------------------------------------------------------------
# Per-collection market stats (floor / volume / listed count / holders)
# ---------------------------------------------------------------------------

_DOWNBAD_FLOOR_RE = re.compile(r"Floor([\d,.]+[kKmM]?)A?")
_DOWNBAD_LISTED_RE = re.compile(r"Listed(\d+)\s*\(([\d.]+)%\)")
_DOWNBAD_VOL_RE = re.compile(r"Total Vol([\d,.]+[kKmM]?)A?")
_DOWNBAD_OWNERS_RE = re.compile(r"Owners(\d+)\s*\(([\d.]+)%\)")
_DOWNBAD_ITEMS_RE = re.compile(r"(\d+) ITEMS")


def _downbad_collection_stats(name: str, playwright_session: Any = None) -> dict[str, Any]:  # noqa: ANN401
    slug = _downbad_discover_slug(name, playwright_session) or _slugify(name)
    text = _render(f"https://www.downbad.farm/collection/{slug}", playwright_session)
    if text is None:
        return {"error": f"could not render downbad.farm/collection/{slug}"}
    if "404" in text[:20] or "could not be found" in text.lower():
        return {"found": False, "tried_slug": slug}
    items_m = _DOWNBAD_ITEMS_RE.search(text)
    floor_m = _DOWNBAD_FLOOR_RE.search(text)
    listed_m = _DOWNBAD_LISTED_RE.search(text)
    vol_m = _DOWNBAD_VOL_RE.search(text)
    owners_m = _DOWNBAD_OWNERS_RE.search(text)
    if not any((items_m, floor_m, listed_m, vol_m, owners_m)):
        # A page that rendered but matched none of the stat patterns is
        # usually the generic app shell, not a real collection page --
        # root-caused 2026-08-11: this used to report found: true with a
        # wall of nulls, which reads as "confirmed empty" when it's really
        # "wrong slug, never saw real content." Report it honestly instead.
        return {"found": False, "tried_slug": slug, "note": "page rendered but no stats matched"}
    return {
        "found": True,
        "tried_slug": slug,
        "items": int(items_m.group(1)) if items_m else None,
        "floor_algo": _parse_amount(floor_m.group(1)) if floor_m else None,
        "listed_count": int(listed_m.group(1)) if listed_m else None,
        "listed_pct": float(listed_m.group(2)) if listed_m else None,
        "total_volume_algo": _parse_amount(vol_m.group(1)) if vol_m else None,
        "owner_count": int(owners_m.group(1)) if owners_m else None,
        "owner_pct": float(owners_m.group(2)) if owners_m else None,
    }


_RAND_FLOOR_RE = re.compile(r"Floor\s*\n([\d,.]+)")
_RAND_HIGHEST_RE = re.compile(r"Highest Sale\s*\n([\d,.]+)")
_RAND_LISTINGS_RE = re.compile(r"Listings\s*\n(\d+)")


def _randgallery_collection_stats(name: str, playwright_session: Any = None) -> dict[str, Any]:  # noqa: ANN401
    from urllib.parse import quote

    text = _render(f"https://www.randgallery.com/collections/{quote(name.strip())}", playwright_session)
    if text is None:
        return {"error": "could not render randgallery.com collection page"}
    floor_m = _RAND_FLOOR_RE.search(text)
    highest_m = _RAND_HIGHEST_RE.search(text)
    listings_m = _RAND_LISTINGS_RE.search(text)
    if not (floor_m or highest_m or listings_m):
        return {"found": False}
    return {
        "found": True,
        "floor_algo": _parse_amount(floor_m.group(1)) if floor_m else None,
        "highest_sale_algo": _parse_amount(highest_m.group(1)) if highest_m else None,
        "listed_count": int(listings_m.group(1)) if listings_m else None,
    }


_EXA_VOLUME_RE = re.compile(r"Total volume\s*\n([\d,.]+[kKmM]?)")
_EXA_FLOOR_RE = re.compile(r"Floor price\s*\n([\d,.]+[kKmM]?|--)")
_EXA_HIGHEST_RE = re.compile(r"Highest sale\s*\n([\d,.]+[kKmM]?|--)")
_EXA_ITEMS_RE = re.compile(r"Items\s*\n(\d+)")


def _exa_collection_stats(name: str, playwright_session: Any = None) -> dict[str, Any]:  # noqa: ANN401
    slug = _slugify(name)
    text = _render(f"https://exa.market/collection/{slug}", playwright_session)
    if text is None:
        return {"error": f"could not render exa.market/collection/{slug}"}
    if "Total volume" not in text:
        return {"found": False, "tried_slug": slug}
    vol_m = _EXA_VOLUME_RE.search(text)
    floor_m = _EXA_FLOOR_RE.search(text)
    highest_m = _EXA_HIGHEST_RE.search(text)
    items_m = _EXA_ITEMS_RE.search(text)
    return {
        "found": True,
        "tried_slug": slug,
        "total_volume_algo": _parse_amount(vol_m.group(1)) if vol_m else None,
        "floor_algo": _parse_amount(floor_m.group(1)) if floor_m else None,
        "highest_sale_algo": _parse_amount(highest_m.group(1)) if highest_m else None,
        "items": int(items_m.group(1)) if items_m else None,
    }


def _statto_collection_stats(name: str, playwright_session: Any = None) -> dict[str, Any]:  # noqa: ANN401
    """Statto has no name-based collection URL (numeric ids only, and the id->name mapping isn't discoverable without already knowing it), so this searches the homepage's 'Top Collections by Volume' ranked table instead -- only covers whatever's rendered there, an UNKNOWN and likely incomplete slice of all collections, never a reliable 'not found' signal."""
    text = _render("https://www.statto.xyz/", playwright_session)
    if text is None:
        return {"error": "could not render statto.xyz"}
    needle = name.strip().lower()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower() == needle:
            window = "\n".join(lines[i : i + 12])
            nums = _NUM_RE.findall(window)
            return {
                "found_in_top_collections_table": True,
                "raw_row_text": window,
                "note": (
                    "parsed from the homepage's visible ranked table only -- "
                    "a collection NOT found here may still exist on Statto, "
                    "just outside whatever page/rank is currently rendered"
                ),
                "numbers_in_row_order": nums[:8],
            }
    return {
        "found_in_top_collections_table": False,
        "note": (
            "not seen in the currently-rendered Top Collections table -- this "
            "does NOT mean the collection is absent from Statto, only that it "
            "wasn't in the visible ranked slice"
        ),
    }


def _tool_nft_collection_market_stats(
    collection: str,
    playwright_session: Any = None,  # noqa: ANN401 -- PlaywrightSession; injected from compose context, see writer_tools._wrap_browser_action
) -> dict[str, Any]:
    """Floor price, total volume, listed count/%, and item/owner counts for an Algorand NFT collection (by its display name), across Downbad, Rand Gallery, Exa Market, and Statto -- for checking a collection-level claim ('sold out', 'X% listed', 'floor of Y ALGO') against real current marketplace data instead of a single site's snapshot or self-reported figures. Each marketplace slugs/matches collection names differently and this tries all four independently -- found: false or a missing section for one marketplace does not mean the collection is absent from Algorand, only from that one site (or that its slug guess didn't match; tried_slug shows what was tried). Slow (multiple page renders) -- expect several seconds."""
    name = (collection or "").strip()
    if not name:
        return {"error": "collection name required"}
    return {
        "collection": name,
        "downbad": _downbad_collection_stats(name, playwright_session),
        "rand_gallery": _randgallery_collection_stats(name, playwright_session),
        "exa_market": _exa_collection_stats(name, playwright_session),
        "statto": _statto_collection_stats(name, playwright_session),
    }


NFT_MARKETPLACE_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "nft_asset_listing_status",
            "description": (
                "Is a specific Algorand NFT (by ASA id) CURRENTLY listed for "
                "sale, and at what price, on Downbad/Statto/Rand Gallery/Exa "
                "Market -- an item can be listed on one marketplace and not "
                "another, so check all four rather than assuming from one. "
                "Slow (several seconds, renders multiple pages)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "integer", "description": "numeric ASA id"},
                },
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "nft_collection_market_stats",
            "description": (
                "Floor price, total volume, listed count/%, item/owner counts "
                "for an Algorand NFT collection by display name, across "
                "Downbad, Rand Gallery, Exa Market, and Statto -- for checking "
                "a collection-level claim ('sold out', 'X% listed', 'floor of "
                "Y ALGO') against real current data. A missing marketplace "
                "section means that site's slug/name match failed, not that "
                "the collection doesn't exist there. Slow (several seconds, "
                "renders multiple pages)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "collection": {
                        "type": "string",
                        "description": "the collection's display name, e.g. 'Pixel City'",
                    },
                },
                "required": ["collection"],
            },
        },
    },
]

NFT_MARKETPLACE_HANDLERS: dict[str, Any] = {
    "nft_asset_listing_status": _tool_nft_asset_listing_status,
    "nft_collection_market_stats": _tool_nft_collection_market_stats,
}


def nft_marketplace_tools() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """NFT marketplace lookup tools (schemas, handlers). Always registered -- every handler degrades to a per-source {"error": ...} rather than raising."""
    return list(NFT_MARKETPLACE_SCHEMAS), dict(NFT_MARKETPLACE_HANDLERS)
