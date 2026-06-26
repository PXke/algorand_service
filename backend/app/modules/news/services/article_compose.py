from __future__ import annotations

import re

from app.core.sanitize import sanitize_markdown_body


def _excerpt(text: str, *, max_chars: int = 900) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars].rsplit(" ", 1)[0]
    return f"{cut}…"


def _editorial_title(*, service_name: str, page_title: str) -> str:
    headline = page_title.strip() or service_name
    if headline.lower() == service_name.lower():
        return f"{service_name} publishes new update"
    generic = headline.lower() in {
        "example domain",
        "home",
        "index",
        "welcome",
        "untitled",
    }
    if generic:
        return f"{service_name} — site update"
    return headline


def compose_article(
    *,
    service_name: str,
    source_url: str,
    page_title: str,
    page_text: str = "",
    txid: str,
    round: int,
    diff: str | None,
    is_first_snapshot: bool,
) -> tuple[str, str, str]:
    """Build editorial title, summary, and markdown body (no LLM)."""
    headline = page_title.strip() or service_name
    title = _editorial_title(service_name=service_name, page_title=page_title)
    excerpt = _excerpt(page_text)

    if is_first_snapshot:
        summary = (
            f"First time on the platform: **{service_name}** — we captured "
            f"“{headline}” and summarized it for readers."
        )
        lead = (
            f"This is the **first article** published for **{service_name}** on the platform. "
            f"We added this website to our monitored sources and recorded its public page "
            f"so future changes can be compared. Below is an editorial summary of what visitors "
            f"see today — not a raw scrape dump."
        )
    elif diff:
        summary = (
            f"{service_name} updated its monitored page (“{headline}”) — "
            "notable content changes detected."
        )
        lead = (
            f"**{service_name}** changed material on its site since the last snapshot. "
            "Below is a readable summary; technical diffs are omitted from the headline."
        )
    else:
        summary = f"{service_name} refreshed “{headline}” on its monitored page."
        lead = (
            f"**{service_name}** published a content refresh. "
            "The page hash changed even though a line-by-line diff was not available."
        )

    body_lines = [
        f"# {headline}",
        "",
        lead,
        "",
    ]

    if excerpt:
        body_lines.extend(["## In brief", "", excerpt, ""])

    if diff and not is_first_snapshot:
        body_lines.extend(
            [
                "## What changed",
                "",
                "The source page changed. Editors can compare versions at the original URL; "
                "a unified diff is stored internally for this publish.",
                "",
            ]
        )

    if source_url.startswith("http"):
        body_lines.extend(
            [
                "## Source",
                "",
                f"[Read the original page]({source_url}) ({service_name}).",
                "",
            ]
        )

    return title, summary, sanitize_markdown_body("\n".join(body_lines))
