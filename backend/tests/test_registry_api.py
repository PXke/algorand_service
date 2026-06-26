from __future__ import annotations

from app.modules.registry.models import ServiceEntry
from types import SimpleNamespace

from app.modules.registry.repository import (
    InMemoryServiceRegistryRepository,
    _row_to_entry,
    set_service_registry_repository,
)
from app.modules.registry.services.registry_service import RegistryService


def test_list_services_includes_source_kind() -> None:
    set_service_registry_repository(
        InMemoryServiceRegistryRepository(
            [
                ServiceEntry(
                    service_id="discord-room-1",
                    display_name="Discord Alpha",
                    match_kind="address",
                    match_value="ADDR",
                    scrape_url="discord://channel/123456789012345678",
                    enabled=True,
                ),
                ServiceEntry(
                    service_id="reddit-community-1",
                    display_name="Reddit r/algorand",
                    match_kind="address",
                    match_value="ADDR",
                    scrape_url="reddit://r/algorand/hot",
                    enabled=True,
                ),
            ]
        )
    )
    items = RegistryService().list_services()
    kinds = {item.service_id: item.source_kind for item in items}
    assert kinds["discord-room-1"] == "discord"
    assert kinds["reddit-community-1"] == "reddit"
    set_service_registry_repository(None)


def test_list_services_excludes_domain_origin_when_seeds_only() -> None:
    set_service_registry_repository(
        InMemoryServiceRegistryRepository(
            [
                ServiceEntry(
                    service_id="reddit-algorand",
                    display_name="Reddit",
                    match_kind="subreddit",
                    match_value="algorand",
                    scrape_url="reddit://r/algorand/new",
                    enabled=True,
                    origin="seed",
                ),
                ServiceEntry(
                    service_id="tinyman-org",
                    display_name="tinyman.org",
                    match_kind="domain",
                    match_value="tinyman.org",
                    scrape_url="https://tinyman.org",
                    enabled=True,
                    origin="domain",
                ),
            ]
        )
    )
    all_items = RegistryService().list_services()
    seed_items = RegistryService().list_services(seeds_only=True)
    assert {item.service_id for item in all_items} == {"reddit-algorand", "tinyman-org"}
    assert {item.service_id for item in seed_items} == {"reddit-algorand"}
    set_service_registry_repository(None)


def test_row_to_entry_infers_domain_origin_from_match_kind() -> None:
    row = SimpleNamespace(
        service_id="tinyman-org",
        display_name=None,
        match_kind="domain",
        match_value=None,
        scrape_url="https://tinyman.org",
        enabled=True,
        origin=None,
    )
    entry = _row_to_entry(row)
    assert entry.origin == "domain"
    assert entry.match_kind == "domain"


def test_row_to_entry_fills_null_domain_fields() -> None:
    row = SimpleNamespace(
        service_id="tinyman-org",
        display_name=None,
        match_kind=None,
        match_value=None,
        scrape_url="https://tinyman.org",
        enabled=True,
        origin="domain",
    )
    entry = _row_to_entry(row)
    assert entry.display_name == "tinyman-org"
    assert entry.match_kind == "domain"
    assert entry.match_value == "https://tinyman.org"
    assert entry.origin == "domain"


def test_list_services_tolerates_incomplete_domain_rows() -> None:
    set_service_registry_repository(
        InMemoryServiceRegistryRepository(
            [
                ServiceEntry(
                    service_id="tinyman-org",
                    display_name="tinyman-org",
                    match_kind="domain",
                    match_value="https://tinyman.org",
                    scrape_url="https://tinyman.org",
                    enabled=True,
                    origin="domain",
                ),
            ]
        )
    )
    items = RegistryService().list_services()
    assert len(items) == 1
    assert items[0].service_id == "tinyman-org"
    set_service_registry_repository(None)
