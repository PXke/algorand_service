"""Domain types for directory listings.

The settlement ledger's SettlementRecord moved to modules/x402/settlement.py
2026-08-30 -- it was never actually directory-specific, see that module's
docstring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import PlatformError, http_status_for_code

# Constant partition key for x402_listings_by_recency. See migration 090 for
# why the whole feed lives in one partition and when to shard it.
DIRECTORY_PARTITION = "default"


class DirectoryError(PlatformError):
    """A directory-flow error mapped to an HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        """Map a directory error code to its HTTP status via http_status_for_code."""
        super().__init__(code, message, http_status=http_status_for_code(code))


@dataclass
class StoredListing:
    """One listed x402 endpoint, as stored and as served by search.

    `payer` is the wallet whose settlement paid for the CURRENT term -- see
    migration 094 and listing_service.py's ownership check. Empty string
    means unowned (a listing created before 094, or a genuine edge case),
    never used as a real wallet's identity.
    """

    url_hash: str
    url: str
    price: str
    description: str
    schema_json: str
    settlement_tx_id: str
    term_end_epoch: int
    created_at_epoch: int
    assets: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    payer: str = ""
