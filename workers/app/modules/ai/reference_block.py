"""Stage-2 assembly helper: append fetched research URLs to a Sources block.

The writer model tends to cite only the main domain in its footer, dropping the
deeper pages it actually fetched during research — which costs citation density
(see gatekeeper.structure.citation_density) and hides real sources from readers.
This deterministically appends every successfully fetched URL the body doesn't
already cite. Pure stdlib so it stays unit-testable without the Mistral client.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Research tools whose results represent a page the model actually retrieved.
_FETCH_TOOLS = {"fetch_url", "fetch_url_safe"}
_SOURCES_HEADING_RE = re.compile(r"(?im)^#{1,6}\s*(sources|references)\b")
_MAX_SOURCES = 12


def fetched_sources(trace: list[dict]) -> list[tuple[str, str]]:
    """(url, label) for each successfully fetched research URL, deduped in order.

    A successful fetch_url result is a dict carrying the (possibly
    redirect-resolved) "url" and no "error"; we prefer its page title as the
    link label, falling back to the host.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in trace:
        if not isinstance(entry, dict) or entry.get("tool") not in _FETCH_TOOLS:
            continue
        result = entry.get("result")
        if not isinstance(result, dict) or result.get("error"):
            continue
        args = entry.get("arguments") if isinstance(entry.get("arguments"), dict) else {}
        url = str(result.get("url") or args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        label = (str(result.get("title") or "").strip() or urlparse(url).netloc)[:120]
        out.append((url, label))
    return out


def append_reference_block(payload: dict[str, Any], trace: list[dict]) -> dict[str, Any]:
    """Return payload with a Sources block listing fetched URLs the body doesn't
    already cite. Non-destructive — existing prose (and any model-written Sources
    section) is preserved; we only append the missing links."""
    body = str(payload.get("body", "") or "").rstrip()
    if not body:
        return payload
    missing = [(url, label) for url, label in fetched_sources(trace) if url not in body]
    missing = missing[:_MAX_SOURCES]
    if not missing:
        return payload
    bullets = "\n".join(f"- [{label}]({url})" for url, label in missing)
    if _SOURCES_HEADING_RE.search(body):
        new_body = f"{body}\n{bullets}\n"
    else:
        new_body = f"{body}\n\n## Sources\n\n{bullets}\n"
    return {**payload, "body": new_body}
