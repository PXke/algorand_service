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


class BoardError(PlatformError):
    """A board-flow error mapped to an HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        """Map a board error code to its HTTP status via http_status_for_code."""
        super().__init__(code, message, http_status=http_status_for_code(code))


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
