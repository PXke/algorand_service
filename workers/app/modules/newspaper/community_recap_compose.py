from __future__ import annotations

import re

from app.modules.newspaper.article_compose import _excerpt


def _first_video_url(text: str) -> str | None:
    match = re.search(
        r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|vimeo\.com/)[^\s\])>\"']+",
        text,
        re.I,
    )
    return match.group(0) if match else None


def compose_community_recap_article(
    *,
    service_name: str,
    source_url: str,
    page_title: str,
    page_text: str,
) -> tuple[str, str, str]:
    """Post-call recap when a recording link appears (transcript phase is future)."""
    headline = page_title.strip() or f"{service_name} community recap"
    video = _first_video_url(page_text)
    title = f"{service_name}: community call recap"
    excerpt = _excerpt(page_text, max_chars=700)

    summary = (
        f"**{service_name}** shared a recording or recap of a recent community session — "
        "summary below; full video on the source link."
    )

    body_lines = [
        f"# {headline}",
        "",
        (
            f"A **community call or live session** from **{service_name}** now has a "
            f"public recording or recap. This article is the second piece in our "
            f"announce → recap lifecycle (see earlier announce posts when published)."
        ),
        "",
        "## Highlights",
        "",
    ]
    if excerpt:
        body_lines.append(excerpt)
    else:
        body_lines.append("Open the source for the full post text.")

    if video:
        body_lines.extend(
            [
                "",
                "## Recording",
                "",
                f"[Watch the recording]({video})",
                "",
            ]
        )

    body_lines.extend(
        [
            "## Source",
            "",
            f"[Original post]({source_url})",
            "",
            "_Automated transcript summaries may be added in a later pipeline phase._",
            "",
        ]
    )
    return title, summary, "\n".join(body_lines)
