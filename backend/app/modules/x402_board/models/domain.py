"""Domain types for paid visibility-board placements.

The settlement ledger is shared infrastructure and lives in
modules/x402/settlement.py -- nothing board-specific about it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import PlatformError, http_status_for_code

# Constant partition key for x402_board_by_recency. See migration 091 for why
# the whole board lives in one partition and when to shard it.
BOARD_PARTITION = "default"

# Fixed board categories (migration 120), the board's own closed enum -- same
# values as x402_directory.models.domain.LISTING_CATEGORIES for vocabulary
# consistency across the marketplace, but a SEPARATE constant, not an import:
# this module's own convention (see normalize_link's docstring) is that the
# board never depends on the directory module's lifecycle, and the two
# enums are free to diverge later without touching each other's code.
# Validated BEFORE the payment gate by board_service.validate_category();
# DEFAULT_BOARD_CATEGORY is what an omitted `category` gets, and what a
# pre-120 row reads back as.
BOARD_CATEGORIES: tuple[str, ...] = (
    "data",
    "ai",
    "finance",
    "identity",
    "storage",
    "compute",
    "social",
    "tooling",
    "other",
)
DEFAULT_BOARD_CATEGORY = "other"


class BoardError(PlatformError):
    """A board-flow error mapped to an HTTP status."""

    def __init__(self, code: str, message: str, *, http_status: int | None = None) -> None:
        """Map a board error code to its HTTP status via http_status_for_code.

        `http_status` overrides the lookup for codes that are this module's
        own (the ownership refusal on renew is a 403, and the shared code
        table in app.core.errors is not this change's to extend).
        """
        super().__init__(code, message, http_status=http_status or http_status_for_code(code))


@dataclass
class StoredPlacement:
    """One paid placement on the board, as stored and as served by the free feed.

    No price, assets or request schema, unlike the directory's StoredListing:
    a placement advertises that something exists, it does not describe
    something callable.

    `payer` is recorded because it is half of the placement's identity (see
    entry_id in the board service) and because it is the only way to tell two
    placements of the same link apart. It is a public Algorand address that
    the payer themselves put on-chain by paying -- not personal data, and it
    is already in the settlement ledger.
    """

    entry_id: str
    link: str
    name: str
    pitch: str
    payer: str
    settlement_tx_id: str
    term_end_epoch: int
    created_at_epoch: int
    # Priority-placement window (migration 114, 2026-09-06). 0 means not
    # boosted. Written only by BoardService.renew() -- repurposed that day
    # from extending term_end (a placement's survival is unaffected by this
    # change: the board is not probed, see workers/app/modules/x402_probe's
    # own scope, so term_end still expires on a fixed schedule) into buying
    # search/board-ranking priority for settings.x402_board_boost_days at a
    # time, max(now, existing.boosted_until_epoch) so an early boost stacks.
    # Read only by BoardService.list_active()'s boosted-first sort.
    boosted_until_epoch: int = 0
    # One of BOARD_CATEGORIES (migration 120); a pre-120 row reads back as
    # DEFAULT_BOARD_CATEGORY. Set only at place/relist time (BoardService.
    # create(), via board_service.validate_category() run pre-gate by the
    # route), never by renew() -- a boost changes ranking priority, not what
    # a tile is about. Read by BoardService.list_active(category=...)'s
    # `?category=` filter.
    category: str = DEFAULT_BOARD_CATEGORY


@dataclass(frozen=True, slots=True)
class StoredClickEvent:
    """One click-through on a placement, for the owner-only click-analytics read.

    Deliberately separate from the x402_board_clicks counter column
    (migration 098): that counter is the free feed's lifetime total, atomic
    and cheap to read for every page of the board. This is a bounded,
    per-click TIME SERIES (migration 120) so an owner can see click activity
    OVER TIME, not just a lifetime number -- see BoardService.click_history().

    `referrer` is the coarse (host-only, never the full URL/query string) form
    of the click-through request's Referer header, or "" when the visitor's
    client sent none -- see api/routes.py's `x402_board_go` for why only the
    host is kept: the full referring URL can carry the visitor's own path or
    query-string state on the linking site, which is not ours to store.
    """

    entry_id: str
    clicked_at_epoch: int
    referrer: str
