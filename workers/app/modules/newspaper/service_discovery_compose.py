from __future__ import annotations

import re

from app.modules.newspaper.article_compose import _excerpt


def _pricing_hints(text: str) -> str | None:
    lower = text.lower()
    hints: list[str] = []
    if any(w in lower for w in ("free", "no cost", "$0")):
        hints.append("mentions free tier or no cost")
    if re.search(r"\$\d", text) or ("algo" in lower and "fee" in lower):
        hints.append("includes concrete pricing or fees")
    if "subscription" in lower or "per month" in lower:
        hints.append("references subscription pricing")
    if not hints:
        return None
    return "; ".join(hints).capitalize() + "."


def compose_service_discovery_article(
    *,
    service_name: str,
    source_url: str,
    page_title: str,
    page_text: str,
) -> tuple[str, str, str]:
    """Editorial profile when the platform discovers or presents a service."""
    headline = page_title.strip() or service_name
    title = f"Discovering {service_name} on Algorand Platform"
    if headline.lower() not in {service_name.lower(), "home", "index"}:
        title = f"{service_name}: {headline}"

    excerpt = _excerpt(page_text, max_chars=700)
    pricing = _pricing_hints(page_text)

    summary = (
        f"We profile **{service_name}** for the community — what it does, "
        f"who it is for, and where to follow the project."
    )

    body_lines = [
        f"# {headline}",
        "",
        (
            f"**Algorand Platform** is now tracking **{service_name}**. "
            f"This article introduces the project for readers who have not seen it yet — "
            f"not a pipeline log, but a short editorial profile."
        ),
        "",
        "## What it is",
        "",
    ]

    if excerpt:
        body_lines.append(excerpt)
    else:
        body_lines.append(
            f"The monitored source ({source_url}) is active; read the original page for full detail."
        )

    body_lines.extend(["", "## Why it matters", ""])
    body_lines.append(
        "Community channels (Reddit, Discord, and project sites) are where new Algorand "
        "tools and services often appear first. We surface them here so builders and users "
        "can compare offerings without hunting every forum thread."
    )

    if pricing:
        body_lines.extend(["", "## Pricing signals", "", pricing])

    body_lines.extend(
        [
            "",
            "## Follow the source",
            "",
            f"[Visit {service_name}]({source_url}) for the latest announcements and documentation.",
            "",
        ]
    )

    return title, summary, "\n".join(body_lines)
