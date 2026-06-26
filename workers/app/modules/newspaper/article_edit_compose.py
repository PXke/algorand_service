from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import MISTRAL_FALLBACK_TEMPLATE, mistral_configured
from app.modules.ai.mistral_client import MistralError
from app.modules.newspaper.article_store import ArticleDetail
from app.modules.newspaper.content_update_compose import _summarize_diff_lines


def _utc_stamp() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def compose_article_edit_template(
    *,
    existing: ArticleDetail,
    new_page_text: str,
    new_page_title: str,
    diff: str | None,
    enrichment_block: str = "",
) -> tuple[str, str, str]:
    """Append ## Updated section with new facts (template path)."""
    bullets = _summarize_diff_lines(diff, max_lines=15)
    lines = [
        "",
        "---",
        "",
        f"## Updated {_utc_stamp()}",
        "",
        "Additional reporting linked to this story:",
        "",
    ]
    if new_page_title.strip():
        lines.append(f"*{new_page_title.strip()}*")
        lines.append("")
    if bullets:
        for b in bullets:
            lines.append(f"- {b}")
        lines.append("")
    else:
        excerpt = new_page_text.strip()[:1200]
        if excerpt:
            lines.append(excerpt)
            lines.append("")

    if enrichment_block.strip():
        lines.append(enrichment_block.strip())
        lines.append("")

    body = (existing.body or "").rstrip() + "\n".join(lines)
    summary = existing.summary
    if "updated" not in summary.lower():
        summary = f"{summary.rstrip()} (updated {_utc_stamp()})"
    title = existing.title
    return title, summary, body


def compose_article_edit(
    *,
    existing: ArticleDetail,
    new_page_text: str,
    new_page_title: str,
    source_url: str,
    diff: str | None,
    enrichment_block: str = "",
    service_name: str = "",
) -> tuple[str, str, str, str]:
    """
    Returns (title, summary, body, composer).
    Prefers Mistral when configured; falls back to template.
    """
    template = compose_article_edit_template(
        existing=existing,
        new_page_text=new_page_text,
        new_page_title=new_page_title,
        diff=diff,
        enrichment_block=enrichment_block,
    )

    if not mistral_configured():
        return (*template, "template")

    try:
        from app.modules.ai.mistral_compose import compose_article_edit_mistral

        fields = compose_article_edit_mistral(
            service_name=service_name or existing.service_id,
            source_url=source_url,
            existing_title=existing.title,
            existing_summary=existing.summary,
            existing_body=existing.body,
            new_page_title=new_page_title,
            new_page_text=new_page_text,
            diff=diff,
            enrichment_block=enrichment_block,
        )
        return fields.title, fields.summary, fields.body, "mistral"
    except MistralError:
        if MISTRAL_FALLBACK_TEMPLATE:
            return (*template, "template")
        raise
