from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    slug: str
    label: str
    description: str
    keywords: frozenset[str]


# Mirror of the Flutter `kNewsSections` (frontend_flutter/lib/modules/newspaper/
# sections.dart) and the publish-pipeline display tags. Sections are derived
# from article tags, so a story surfaces here when any tag matches `keywords`.
SECTIONS: tuple[Section, ...] = (
    Section(
        slug="markets",
        label="Markets",
        description="Algorand price action, market data and weekly digests.",
        keywords=frozenset({"market", "pricing", "price", "weekly", "digest"}),
    ),
    Section(
        slug="security",
        label="Security",
        description="Scam alerts, outages and security incidents across the Algorand ecosystem.",
        keywords=frozenset({"scam-alert", "scam", "outage", "incident", "breaking"}),
    ),
    Section(
        slug="developers",
        label="Developers",
        description="SDK releases, tooling and developer news for building on Algorand.",
        keywords=frozenset({"sdk", "release", "ai"}),
    ),
    Section(
        slug="community",
        label="Community",
        description="Events, recaps and community happenings in the Algorand world.",
        keywords=frozenset({"community", "recap", "event"}),
    ),
    Section(
        slug="ecosystem",
        label="Ecosystem",
        description="New projects, launches and partnerships across the Algorand ecosystem.",
        keywords=frozenset({"discovery", "update", "new-service", "launch", "partnership"}),
    ),
)

_BY_SLUG = {s.slug: s for s in SECTIONS}


def section_for_slug(slug: str) -> Section | None:
    return _BY_SLUG.get(slug.strip().lower())


def matches_section(section: Section, tags: list[str]) -> bool:
    lowered = {t.lower().strip() for t in tags}
    return bool(lowered & section.keywords)
