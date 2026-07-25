"""Extract addresses/domains and gather context for scam/incident articles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# Phase 2: safe fetch of mentioned domains, search API, internal article index.
SCAM_ENRICHMENT_ENABLED = False


_ALGO_ADDRESS = re.compile(r"\b[A-Z2-7]{58}\b")


@dataclass(frozen=True)
class ScamEnrichmentContext:
    """Evidence bundle for Mistral/template — never trust raw alert text alone."""

    mentioned_urls: tuple[str, ...] = ()
    mentioned_domains: tuple[str, ...] = ()
    mentioned_algo_addresses: tuple[str, ...] = ()
    internal_matches: tuple[str, ...] = ()
    external_snippets: tuple[str, ...] = ()
    fetch_notes: tuple[str, ...] = ()


def _defang_text(text: str) -> str:
    """algoblow[.]com → algoblow.com for domain extraction."""
    return text.replace("[.]", ".").replace("(.)", ".")


def extract_algorand_addresses(page_text: str) -> list[str]:
    """Pull unique 58-char Algorand addresses out of the text, order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _ALGO_ADDRESS.finditer(page_text.upper()):
        addr = match.group(0)
        if addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def extract_domains_and_urls(page_text: str) -> tuple[list[str], list[str]]:
    """Extract (URLs, host domains) from text, de-defanging obfuscated dots first."""
    normalized = _defang_text(page_text)
    urls = re.findall(r"https?://[^\s\])>\"']+", normalized)
    domains: list[str] = []
    for url in urls:
        try:
            host = urlparse(url).netloc.lower()
            if host and host not in domains:
                domains.append(host)
        except Exception:
            continue
    bare = re.findall(
        r"\b((?:[a-z0-9][-a-z0-9]*\.)+(?:com|org|io|net|app|xyz|finance|exchange))\b",
        normalized.lower(),
    )
    for d in bare:
        if d not in domains:
            domains.append(d)
    return urls, domains


def gather_scam_enrichment(page_text: str, *, source_url: str = "") -> ScamEnrichmentContext:  # noqa: ARG001 -- name must match the real callee's keyword arg
    """Cross-reference scam alerts before composition.

    Today: extract domains/URLs only (no live fetch — avoids publishing from unverified
    malicious pages). Next: allowlisted safe preview fetch + search API + Typesense.
    """
    urls, domains = extract_domains_and_urls(page_text)
    algo_addrs = extract_algorand_addresses(page_text)
    notes: list[str] = []
    if algo_addrs:
        notes.append(
            f"reported_rekeyed_or_affected_accounts: {len(algo_addrs)} on-chain address(es)"
        )
    if not SCAM_ENRICHMENT_ENABLED:
        if domains:
            notes.append(
                "enrichment_disabled: domains noted for manual review — " + ", ".join(domains[:8])
            )
        return ScamEnrichmentContext(
            mentioned_urls=tuple(urls[:20]),
            mentioned_domains=tuple(domains[:20]),
            mentioned_algo_addresses=tuple(algo_addrs[:20]),
            fetch_notes=tuple(notes),
        )

    # Future: HTTP HEAD/GET on allowlisted inspector, Google Programmable Search, etc.
    return ScamEnrichmentContext(
        mentioned_urls=tuple(urls[:20]),
        mentioned_domains=tuple(domains[:20]),
        mentioned_algo_addresses=tuple(algo_addrs[:20]),
        fetch_notes=tuple(notes) if notes else ("enrichment_stub",),
    )


def format_enrichment_for_prompt(ctx: ScamEnrichmentContext) -> str:
    """Render a ScamEnrichmentContext as a markdown block for the writer prompt."""
    lines = ["## Verification context (do not repeat scam instructions verbatim)"]
    if ctx.mentioned_domains:
        lines.append("Domains mentioned: " + ", ".join(ctx.mentioned_domains))
    if ctx.mentioned_algo_addresses:
        lines.append(
            "Algorand accounts cited in report (likely victims rekeyed — verify on explorer): "
            + ", ".join(ctx.mentioned_algo_addresses[:8])
        )
        if len(ctx.mentioned_algo_addresses) > 8:
            lines.append(f"… and {len(ctx.mentioned_algo_addresses) - 8} more")
    if ctx.internal_matches:
        lines.append("Prior platform coverage: " + "; ".join(ctx.internal_matches))
    if ctx.external_snippets:
        lines.append("External references: " + "; ".join(ctx.external_snippets[:3]))
    if ctx.fetch_notes:
        lines.append("Notes: " + "; ".join(ctx.fetch_notes))
    return "\n".join(lines)
