"""Stage-2 assembly helper: append fetched research URLs to a Sources block.

The writer model tends to cite only the main domain in its footer, dropping the
deeper pages it actually fetched during research — which costs citation density
(see gatekeeper.structure.citation_density) and hides real sources from readers.
This deterministically appends every successfully fetched URL the body doesn't
already cite. Pure stdlib so it stays unit-testable without the LLM client.

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

# Research tools whose results represent ONE page/record the model actually
# retrieved, mapped to the result-dict field holding its citable url. "url"
# for almost all of them; search_nfd_directory is the one exception, its own
# "url" field being the NFD OWNER's self-declared site, not a citation for
# the lookup itself (see that tool's own docstring) -- nfd_profile_url is.
# 2026-09-08: extended past fetch_url/search_web to every other research
# tool that returns a real per-fact URL -- same "trust the number, no way
# to click through and check it" gap chain_entity_gate.py's 'app' entity
# kind fix closed for on-chain citations the same day. On-chain facts get
# that module's inline auto-linking instead of this Sources-block backfill
# (a better-fitting mechanism for an entity id mentioned in prose); this
# backfill is for whole external pages/records a tool fetched.
_FETCH_TOOLS: dict[str, str] = {
    "fetch_url": "url",
    "fetch_url_safe": "url",
    "github_activity": "url",
    "get_defi_tvl": "url",
    "get_defi_tvl_history": "url",
    "package_download_stats": "url",
    "search_nfd_directory": "nfd_profile_url",
}
# Tools that only return candidate hits (several items with their own
# label/url) the model may not have actually used -- used for the narrower
# cited-domain backfill only. (result list key, ordered label-field
# fallbacks) per tool: search_x's posts have no "title", only "text"; the
# rest name their own item shape.
_SEARCH_TOOL_SHAPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "search_web": ("results", ("title",)),
    "search_x": ("posts", ("text",)),
    "app_store_metrics": ("results", ("app_name",)),
    "lookup_asset_market_data": ("assets", ("name", "ticker")),
}
_SOURCES_HEADING_RE = re.compile(r"(?im)^#{1,6}\s*(sources?|references?)\b")
_LINK_URL_RE = re.compile(r"\]\((https?://[^\s)]+)\)")
_MAX_SOURCES = 12


def _title_or_host_label(title: object, url: str) -> str:
    """The citation label for one source: the tool's own title, unless that title IS the url (or another url) -- some fetch paths (e.g. fetch_url's JSON/XML branches, which have no real <title> to extract) stash the url itself in the title field as a display placeholder for THEIR OWN purposes. Using that verbatim here produces a citation whose visible text is a 120-char-truncated URL, ending mid-query-string -- confirmed live 2026-09-08 on a raw indexer fetch. Fall back to the host in that case, same as a genuinely absent title."""
    label = str(title or "").strip()
    if not label or label.startswith(("http://", "https://")):
        label = urlparse(url).netloc
    return label[:120]


def fetched_sources(trace: list[dict]) -> list[tuple[str, str]]:
    """(url, label) for each successfully fetched/looked-up research source, deduped in order.

    A successful result is a dict with no "error", carrying its citable url
    under whichever field _FETCH_TOOLS names for that tool (almost always
    "url"); we prefer its title field as the link label, falling back to the
    host.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in trace:
        if not isinstance(entry, dict):
            continue
        url_field = _FETCH_TOOLS.get(entry.get("tool"))
        if url_field is None:
            continue
        result = entry.get("result")
        if not isinstance(result, dict) or result.get("error"):
            continue
        args = entry.get("arguments") if isinstance(entry.get("arguments"), dict) else {}
        url = str(result.get(url_field) or args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        out.append((url, _title_or_host_label(result.get("title"), url)))
    return out


def _search_result_sources(trace: list[dict]) -> list[tuple[str, str]]:
    """(url, label) for every search-shaped tool hit, deduped in order. Unfiltered — callers must narrow this down (see cited-domain backfill below); most search hits are never actually used by the model."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in trace:
        if not isinstance(entry, dict):
            continue
        shape = _SEARCH_TOOL_SHAPES.get(entry.get("tool"))
        if shape is None:
            continue
        list_key, label_keys = shape
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        for item in result.get(list_key) or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            label = ""
            for key in label_keys:
                label = str(item.get(key) or "").strip()
                if label:
                    break
            out.append((url, _title_or_host_label(label, url)))
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
