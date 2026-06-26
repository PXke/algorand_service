from __future__ import annotations

from app.modules.registry.models import ServiceRegistryItem
from app.modules.registry.repository import get_service_registry_repository
from app.modules.registry.source_kind import scrape_source_kind


class RegistryService:
    def list_services(self, *, seeds_only: bool = False) -> list[ServiceRegistryItem]:
        entries = get_service_registry_repository().list_all()
        if seeds_only:
            entries = [entry for entry in entries if _is_seed(entry)]
        items = [
            ServiceRegistryItem(
                service_id=entry.service_id,
                display_name=entry.display_name,
                match_kind=entry.match_kind,
                match_value=entry.match_value,
                scrape_url=entry.scrape_url,
                enabled=entry.enabled,
                source_kind=scrape_source_kind(entry.scrape_url),
                origin=getattr(entry, "origin", "seed"),
            )
            for entry in entries
        ]
        items.sort(key=lambda item: item.display_name.lower())
        return items


def _is_seed(entry) -> bool:
    """Seeds tab: manually seeded or admin-added sources, not frontier-promoted domains."""
    return getattr(entry, "origin", "seed") != "domain"
