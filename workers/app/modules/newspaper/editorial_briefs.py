from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EditorialBriefMatch:
    brief_id: str
    title: str
    body_markdown: str


def _keyword_tokens(keywords: str) -> list[str]:
    parts = re.split(r"[,;\s]+", (keywords or "").strip().lower())
    return [p for p in parts if len(p) >= 2]


def brief_matches_text(*, keywords: str, haystack: str) -> bool:
    tokens = _keyword_tokens(keywords)
    if not tokens:
        return False
    text = haystack.lower()
    return any(token in text for token in tokens)


def format_briefs_for_writer(matches: list[EditorialBriefMatch]) -> str:
    if not matches:
        return ""
    lines = ["### Editorial briefs (admin suggestions)"]
    for brief in matches[:3]:
        lines.append(f"- **{brief.title}** (`{brief.brief_id}`)")
        body = (brief.body_markdown or "").strip()
        if body:
            snippet = body if len(body) <= 800 else body[:800] + "…"
            lines.append(f"  {snippet}")
    lines.append(
        "Use these as editorial direction only; verify facts from sources and enrichment."
    )
    return "\n".join(lines)


def find_matching_queued_briefs(
    *,
    page_text: str,
    page_title: str = "",
    publish_topic: str = "",
    limit: int = 20,
) -> list[EditorialBriefMatch]:
    """
    Load queued editorial_briefs and return those whose keywords appear in ingest text.
    """
    from app.core.cassandra import get_cassandra_session

    haystack = " ".join([page_title, page_text, publish_topic])
    session = get_cassandra_session()
    try:
        rows = session.execute(
            """
            SELECT brief_id, title, body_markdown, keywords, status
            FROM editorial_briefs LIMIT %s
            """,
            (limit,),
        )
    except Exception:
        return []

    matches: list[EditorialBriefMatch] = []
    for row in rows:
        status = (row.status or "").strip().lower()
        if status not in ("queued", "open", "active"):
            continue
        keywords = row.keywords or ""
        if not brief_matches_text(keywords=keywords, haystack=haystack):
            continue
        matches.append(
            EditorialBriefMatch(
                brief_id=str(row.brief_id),
                title=row.title or "",
                body_markdown=row.body_markdown or "",
            )
        )
    return matches


def load_editorial_brief_block(
    *,
    page_text: str,
    page_title: str = "",
    publish_topic: str = "",
) -> str:
    matches = find_matching_queued_briefs(
        page_text=page_text,
        page_title=page_title,
        publish_topic=publish_topic,
    )
    return format_briefs_for_writer(matches)
