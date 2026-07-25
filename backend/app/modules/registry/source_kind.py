"""Classify a service's scrape URL into a source kind (web, mail, chain, etc.)."""

from __future__ import annotations


def scrape_source_kind(scrape_url: str | None) -> str:
    """Classify scrape_url for API and UI (discord, reddit, web, chain_only)."""
    if not scrape_url or not scrape_url.strip():
        return "chain_only"
    raw = scrape_url.strip().lower()
    if raw.startswith("discord:"):
        return "discord"
    if raw.startswith("reddit:"):
        return "reddit"
    if raw.startswith("telegram:"):
        return "telegram"
    if raw.startswith("youtube:"):
        return "youtube"
    if raw.startswith("mail:"):
        return "mail"
    if raw.startswith("push:") or raw.startswith("ingest:"):
        return "push"
    return "web"
