"""Assess whether a breaking-tier draft is credible enough to auto-publish."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BreakingAssessment:
    """Whether a breaking-tier draft is credible enough to auto-publish."""

    credible: bool
    reason: str
    method: str


def extract_urls(text: str) -> list[str]:
    """Pull all http(s) URLs out of free text."""
    return re.findall(r"https?://[^\s\])>\"']+", text)


def assess_breaking_credibility(
    *,
    page_text: str,
    source_url: str,
    topic: str,  # noqa: ARG001 -- name must match the real callee's keyword arg
) -> BreakingAssessment:
    """Gate breaking publishes using lightweight heuristics (links + loss/scam language). Mistral is reserved for article composition and is intentionally not used here."""
    urls = extract_urls(page_text)
    if not urls and source_url.startswith("http"):
        urls = [source_url]

    return _assess_heuristic(page_text=page_text, urls=urls)


def _assess_heuristic(*, page_text: str, urls: list[str]) -> BreakingAssessment:
    lower = page_text.lower()
    has_alert = any(
        p in lower
        for p in (
            "scam",
            "phishing",
            "exploit",
            "malicious",
            "do not interact",
            "do not connect",
            "rekey",
            "warning",
            "lost",
            "stolen",
            "halt",
            "outage",
            "down",
            "emergency",
        )
    )
    has_link = len(urls) > 0
    has_amount = bool(re.search(r"\$[\d,]+|\d+\s*algo", lower, re.I))

    if has_alert and (has_link or has_amount or len(page_text) > 120):
        return BreakingAssessment(
            credible=True,
            reason="heuristic_alert_with_evidence",
            method="heuristic",
        )
    if has_alert:
        return BreakingAssessment(
            credible=False,
            reason="heuristic_alert_without_evidence",
            method="heuristic",
        )
    return BreakingAssessment(credible=False, reason="heuristic_not_breaking", method="heuristic")
