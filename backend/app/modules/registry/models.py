"""Registry models. ServiceRegistryItem (wire schema) lives in app/schemas.py and is re-exported here; the two frozen dataclasses below are internal value objects (never serialised over the API) and stay local."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas import ServiceRegistryItem  # noqa: F401


@dataclass(frozen=True)
class ServiceEntry:
    """One entry in the service registry."""

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
