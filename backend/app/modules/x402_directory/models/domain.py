"""Domain types for directory listings and the settlement ledger."""

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
    """One listed x402 endpoint, as stored and as served by search."""

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


@dataclass
class SettlementRecord:
    """One settled x402 payment, for the bookkeeping ledger (CLAUDE.md section 9)."""

    tx_id: str
    asset_id: str
    amount_atomic: str
    payer: str
    resource: str
    network: str
    settled_at_epoch: int
    # EUR value at settlement time. Always 0.0 today: no FX lookup is built, and
    # a visible zero is preferable to an absent column that later reads as
    # "this settlement had no value".
    eur_value: float = 0.0
