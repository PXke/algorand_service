from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class ServiceEntry:
    service_id: str
    display_name: str
    match_kind: str
    match_value: str
    scrape_url: str | None
    enabled: bool = True
    origin: str = "seed"


@dataclass(frozen=True)
class ChainTransaction:
    """Minimal txn view for registry matching (from Conduit index)."""

    txid: str
    round: int
    sender: str
    txn_type: str
    receiver: str | None = None
    amount_microalgos: int | None = None
    txn_json: str | None = None


class ServiceRegistryItem(BaseModel):
    service_id: str
    display_name: str
    match_kind: str
    match_value: str
    scrape_url: str | None = None
    enabled: bool = True
    source_kind: str = "web"
    origin: str = "seed"
