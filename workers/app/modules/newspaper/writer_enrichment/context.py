from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WriterEnrichmentBundle:
    """
    Evidence for Mistral/templates — discovery vs update vs scam verification.
    """

    service_id: str
    phase: str  # discovery | update | scam_alert
    primary_domain: str = ""
    is_first_snapshot: bool = True
    sections: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "phase": self.phase,
            "primary_domain": self.primary_domain,
            "is_first_snapshot": self.is_first_snapshot,
            "sections": self.sections,
            "warnings": self.warnings,
        }
