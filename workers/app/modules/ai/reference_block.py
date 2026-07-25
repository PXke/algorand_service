"""Stage-2 assembly helper: append fetched research URLs to a Sources block.

The writer model tends to cite only the main domain in its footer, dropping the
deeper pages it actually fetched during research — which costs citation density
(see gatekeeper.structure.citation_density) and hides real sources from readers.
This deterministically appends every successfully fetched URL the body doesn't
already cite. Pure stdlib so it stays unit-testable without the Mistral client.

Same failure mode also happens for sources the model only ever saw as a
search_web hit (title + url + snippet) and never actually opened with
fetch_url — e.g. it read a search snippet naming an MSN article, then wrote
"https://www.msn.com" (the bare domain) in its own footer instead of the
specific article path from the search result (root-caused 2026-07-21). We
can't treat every search_web hit as a citable source — most are never used —
so the backfill is narrow: only upgrade a domain the body ALREADY cites (the
model's own signal that it meant to cite something there), never a domain it
never mentioned.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Research tools whose results represent a page the model actually retrieved.
_FETCH_TOOLS = {"fetch_url", "fetch_url_safe"}
# Tools that only return candidate hits (title/url/snippet) the model may not
# have actually opened — used for the narrower cited-domain backfill only.
_SEARCH_TOOLS = {"search_web"}
_SOURCES_HEADING_RE = re.compile(r"(?im)^#{1,6}\s*(sources?|references?)\b")
_LINK_URL_RE = re.compile(r"\]\((https?://[^\s)]+)\)")
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


def _search_result_sources(trace: list[dict]) -> list[tuple[str, str]]:
    """(url, label) for every search_web hit, deduped in order. Unfiltered — callers must narrow this down (see cited-domain backfill below); most search hits are never actually used by the model."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in trace:
        if not isinstance(entry, dict) or entry.get("tool") not in _SEARCH_TOOLS:
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        for item in result.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            label = (str(item.get("title") or "").strip() or urlparse(url).netloc)[:120]
            out.append((url, label))
    return out


def _cited_domains(body: str) -> set[str]:
    return {urlparse(url).netloc for url in _LINK_URL_RE.findall(body)}


def append_reference_block(payload: dict[str, Any], trace: list[dict]) -> dict[str, Any]:
    """Return payload with a Sources block listing fetched URLs the body doesn't already cite. Non-destructive — existing prose (and any model-written Sources section) is preserved; we only append the missing links."""
    body = str(payload.get("body", "") or "").rstrip()
    if not body:
        return payload
    missing = [(url, label) for url, label in fetched_sources(trace) if url not in body]

    # Backfill search_web hits, but only to upgrade a domain the model already
    # (imprecisely) cited — never to introduce a source it never mentioned.
    # One upgrade per domain: the first matching search hit, not every one.
    already_present = {url for url, _ in missing}
    cited_domains = _cited_domains(body)
    upgraded_domains: set[str] = set()
    for url, label in _search_result_sources(trace):
        if url in body or url in already_present:
            continue
        netloc = urlparse(url).netloc
        if netloc not in cited_domains or netloc in upgraded_domains:
            continue
        missing.append((url, label))
        already_present.add(url)
        upgraded_domains.add(netloc)

    missing = missing[:_MAX_SOURCES]
    if not missing:
        return payload
    bullets = "\n".join(f"- [{label}]({url})" for url, label in missing)
    if _SOURCES_HEADING_RE.search(body):
        new_body = f"{body}\n{bullets}\n"
    else:
        new_body = f"{body}\n\n## Sources\n\n{bullets}\n"
    return {**payload, "body": new_body}
