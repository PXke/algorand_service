from __future__ import annotations

from typing import Any


def diff_against_stored_intelligence(
    *,
    previous: dict[str, Any] | None,
    current_domains: list[str],
    current_primary: str,
    text_diff: str | None,
) -> dict[str, Any]:
    """What changed since last enrichment snapshot."""
    if previous is None:
        return {"kind": "first_intelligence", "text_diff_lines": _count_diff_lines(text_diff)}

    prev_domain = str(previous.get("primary_domain", ""))
    prev_domains = list(previous.get("domains", []))
    added = [d for d in current_domains if d not in prev_domains]
    removed = [d for d in prev_domains if d not in current_domains]
    domain_changed = prev_domain and current_primary and prev_domain != current_primary

    return {
        "kind": "update",
        "domain_changed": domain_changed,
        "previous_primary_domain": prev_domain,
        "current_primary_domain": current_primary,
        "domains_added": added[:10],
        "domains_removed": removed[:10],
        "text_diff_lines": _count_diff_lines(text_diff),
    }


def _count_diff_lines(text_diff: str | None) -> int:
    if not text_diff:
        return 0
    return sum(
        1 for line in text_diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
