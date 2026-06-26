from __future__ import annotations

import re


def score_service_impressiveness(*, page_text: str, source_url: str = "") -> tuple[int, str]:
    """
    Heuristic service weight for queue priority (−20 .. +15).
    Thin placeholder sites rank below rich docs.
    """
    text = page_text.strip()
    lower = text.lower()
    chars = len(text)

    score = 0
    reasons: list[str] = []

    if chars >= 8000:
        score += 15
        reasons.append("long_content")
    elif chars >= 2000:
        score += 10
        reasons.append("substantial_content")
    elif chars >= 800:
        score += 5
        reasons.append("moderate_content")
    elif chars < 500:
        score -= 20
        reasons.append("thin_page")

    headings = len(re.findall(r"^#{1,3}\s", text, re.M))
    if headings >= 5:
        score += 4
        reasons.append("structured_headings")

    link_hints = ("docs.", "/docs", "github.com", "pricing", "api.", "developer")
    if any(h in lower or h in source_url.lower() for h in link_hints):
        score += 5
        reasons.append("product_links")

    if any(p in lower for p in ("lorem ipsum", "coming soon", "under construction")):
        score -= 10
        reasons.append("placeholder_copy")

    score = max(-20, min(15, score))
    return score, ",".join(reasons) if reasons else "neutral"
