from __future__ import annotations

from app.modules.newspaper.article_compose import _editorial_title, _excerpt
from app.modules.newspaper.publish_policy import PublishTopic


def _summarize_diff_lines(diff: str | None, *, max_lines: int = 12) -> list[str]:
    if not diff:
        return []
    bullets: list[str] = []
    for raw in diff.splitlines():
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = raw[1:].strip()
        if not line or len(line) < 4:
            continue
        bullets.append(line[:200])
        if len(bullets) >= max_lines:
            break
    return bullets


def _change_headline(*, service_name: str, topic: PublishTopic, page_title: str) -> str:
    headline = page_title.strip() or service_name
    if topic == PublishTopic.PRICING_CHANGE:
        return f"{service_name} updates pricing or fees"
    if topic == PublishTopic.SDK_RELEASE:
        return f"{service_name} ships an SDK or release"
    if topic == PublishTopic.COMMUNITY_EVENT:
        return f"{service_name} announces a community event"
    return _editorial_title(service_name=service_name, page_title=headline)


def compose_content_update_article(
    *,
    service_name: str,
    source_url: str,
    page_title: str,
    page_text: str,
    diff: str | None,
    topic: PublishTopic = PublishTopic.CONTENT_UPDATE,
) -> tuple[str, str, str]:
    """Editorial 'was / now' framing for meaningful crawl diffs."""
    headline = page_title.strip() or service_name
    title = _change_headline(service_name=service_name, topic=topic, page_title=page_title)
    excerpt = _excerpt(page_text, max_chars=800)
    added = _summarize_diff_lines(diff)

    topic_phrase = {
        PublishTopic.PRICING_CHANGE: "pricing or fee information",
        PublishTopic.SDK_RELEASE: "SDK or release documentation",
        PublishTopic.COMMUNITY_EVENT: "community event details",
        PublishTopic.CONTENT_UPDATE: "public page content",
    }.get(topic, "public page content")

    summary = (
        f"**{service_name}** changed {topic_phrase} on its monitored source "
        f"— here is what readers should know."
    )

    body_lines = [
        f"# {headline}",
        "",
        (
            f"**{service_name}** previously published one version of this page on "
            f"Algorand Platform; after a new crawl we detected a **material change** "
            f"in {topic_phrase}. This article explains the shift in plain language — "
            f"not a raw diff dump."
        ),
        "",
        "## What changed",
        "",
    ]

    if topic == PublishTopic.PRICING_CHANGE:
        body_lines.append(
            "The source now reflects **different pricing or fee language** than the last "
            "snapshot. Compare the original URL if you rely on this service commercially."
        )
    elif topic == PublishTopic.SDK_RELEASE:
        body_lines.append(
            "Release-oriented lines appeared on the monitored source — likely a **new SDK "
            "version, changelog, or GitHub release** worth tracking for integrators."
        )
    elif topic == PublishTopic.COMMUNITY_EVENT:
        body_lines.append(
            "Event-oriented wording showed up — for example a **community call, AMA, or "
            "scheduled session**. Check dates on the source; times may be in the poster's timezone."
        )
    else:
        body_lines.append(
            "The monitored page **no longer matches** the previous snapshot. "
            "Below are highlights from newly added lines; removed text is omitted "
            "from this summary."
        )

    if added:
        body_lines.extend(["", "### New or updated lines (excerpt)", ""])
        for line in added:
            body_lines.append(f"- {line}")
    elif excerpt:
        body_lines.extend(["", "### Current page (excerpt)", "", excerpt])

    if excerpt and added:
        body_lines.extend(["", "## Context now", "", excerpt])

    if source_url.startswith("http") or "://" in source_url:
        body_lines.extend(
            [
                "",
                "## Source",
                "",
                f"[Read the current page]({source_url}) ({service_name}).",
                "",
            ]
        )

    return title, summary, "\n".join(body_lines)


def compose_scam_alert_article(
    *,
    service_name: str,
    source_url: str,
    page_title: str,
    page_text: str,
) -> tuple[str, str, str]:
    """Urgent community safety copy when Discord/Reddit/web mentions a scam."""
    title = f"Scam alert linked to {service_name}"
    excerpt = _excerpt(page_text, max_chars=600)

    summary = (
        f"A monitored **{service_name}** channel posted language that matches "
        "**scam or phishing warnings** — treat wallets and links with extra care."
    )

    body_lines = [
        "# Scam alert",
        "",
        (
            f"Algorand Platform flagged **high-risk wording** on a source we track for "
            f"**{service_name}**. This is editorial coverage of a community warning — "
            f"not legal advice. **Verify** any claim on official project channels before acting."
        ),
        "",
        "## What was posted",
        "",
    ]
    if excerpt:
        body_lines.append(excerpt)
    else:
        body_lines.append("See the linked source for the full warning text.")

    body_lines.extend(
        [
            "",
            "## What you should do",
            "",
            "- Do not approve unknown transactions or connect wallets to unverified sites.",
            "- Cross-check addresses and links with official announcements.",
            "- Report impersonation to the platform moderators when applicable.",
            "",
            "## Source",
            "",
            f"[View the monitored post]({source_url}).",
            "",
        ]
    )
    return title, summary, "\n".join(body_lines)
