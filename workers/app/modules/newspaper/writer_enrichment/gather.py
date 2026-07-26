"""Gather and format all pre-compose enrichment collectors into one context."""

from __future__ import annotations

from app.core import config
from app.modules.newspaper.publish_policy import PublishTopic
from app.modules.newspaper.scam_enrichment import (
    extract_domains_and_urls,
    gather_scam_enrichment,
)
from app.modules.newspaper.scam_enrichment import (
    format_enrichment_for_prompt as format_scam_block,
)
from app.modules.newspaper.writer_enrichment.collectors.app_stores import detect_app_store_links
from app.modules.newspaper.writer_enrichment.collectors.chain_activity import collect_chain_context
from app.modules.newspaper.writer_enrichment.collectors.domain_probe import (
    primary_domain_from_source,
    probe_domain,
)
from app.modules.newspaper.writer_enrichment.collectors.internal_db import collect_internal_context
from app.modules.newspaper.writer_enrichment.collectors.internal_search import (
    search_platform_mentions,
)
from app.modules.newspaper.writer_enrichment.collectors.profile_diff import (
    diff_against_stored_intelligence,
)
from app.modules.newspaper.writer_enrichment.collectors.social_signals import collect_social_signals
from app.modules.newspaper.writer_enrichment.context import WriterEnrichmentBundle
from app.modules.newspaper.writer_enrichment.intelligence_store import (
    load_intelligence,
    save_intelligence,
)


def gather_writer_enrichment(
    *,
    service_id: str,
    display_name: str,
    source_url: str,
    page_text: str,
    page_title: str = "",
    diff: str | None = None,
    is_first_snapshot: bool = False,
    publish_topic: PublishTopic | None = None,
    match_kind: str = "",
    match_value: str = "",
) -> WriterEnrichmentBundle:
    """Build a writer context bundle: discovery intel, update diffs, scam cross-refs.

    Fed to Mistral/templates — not shown raw to readers.
    """
    topic = publish_topic or PublishTopic.GENERIC
    if topic == PublishTopic.SCAM_ALERT:
        phase = "scam_alert"
    elif is_first_snapshot:
        phase = "discovery"
    else:
        phase = "update"

    bundle = WriterEnrichmentBundle(
        service_id=service_id,
        phase=phase,
        is_first_snapshot=is_first_snapshot,
    )

    _urls, domains = extract_domains_and_urls(page_text)
    primary = primary_domain_from_source(source_url, page_text)
    bundle.primary_domain = primary
    bundle.sections["domains"] = {"listed": domains, "primary": primary}

    previous = load_intelligence(service_id)
    bundle.sections["profile_diff"] = diff_against_stored_intelligence(
        previous=previous,
        current_domains=domains,
        current_primary=primary,
        text_diff=diff,
    )

    bundle.sections["internal"] = collect_internal_context(service_id=service_id)
    # Market context (ALGO price/mcap/volume) is deliberately NOT injected here.
    # It used to be handed to the writer unconditionally, which bypassed the
    # system prompt's own "ALGO PRICE/MARKET RULE" ("fetch and mention ONLY
    # when the metric materially helps THIS story... when in doubt, leave it
    # out") — the model had no reason to exercise that judgment when the data
    # was free. The writer already has the get_algo_market tool (writer_tools.py)
    # to fetch the exact same data itself when it decides a story genuinely
    # needs it; a tool call also lands in the research trace, so the gatekeeper's
    # numeric-entailment check can actually verify it (root-caused 2026-07-14:
    # this unconditional injection was invisible to that check, producing a
    # false-positive "ungrounded figures" flag on numbers that were correct).
    bundle.sections["app_stores"] = detect_app_store_links(page_text, source_url)
    bundle.sections["chain"] = collect_chain_context(
        service_id=service_id,
        match_kind=match_kind,
        match_value=match_value,
    )
    bundle.sections["social"] = collect_social_signals(
        service_id=service_id,
        primary_domain=primary,
        display_name=display_name,
        page_text=page_text,
    )
    bundle.sections["platform_search"] = search_platform_mentions(
        service_id=service_id,
        primary_domain=primary,
        display_name=display_name,
    )

    if (
        config.WRITER_ENRICHMENT_ENABLED
        and config.WRITER_ENRICHMENT_PROBE_DOMAIN
        and primary
        and _should_probe_domain(source_url)
    ):
        bundle.sections["domain_probe"] = probe_domain(primary)
        if bundle.sections["domain_probe"].get("safety_hint") in (
            "unreachable_or_tls_issue",
            "no_https",
            "http_error",
        ):
            bundle.warnings.append(f"domain_probe:{bundle.sections['domain_probe']['safety_hint']}")

    bundle.sections["whois"] = {
        "registration_date": "not_implemented",
        "registrant_type": "not_implemented",
        "note": "RDAP/WHOIS API phase 3",
    }

    if phase == "scam_alert":
        scam = gather_scam_enrichment(page_text, source_url=source_url)
        bundle.sections["scam"] = {
            "domains": list(scam.mentioned_domains),
            "urls": list(scam.mentioned_urls),
            "algo_addresses": list(scam.mentioned_algo_addresses),
            "notes": list(scam.fetch_notes),
        }
        if scam.mentioned_domains and primary and scam.mentioned_domains[0] != primary:
            bundle.warnings.append("scam_domain_differs_from_service_primary")

    bundle.sections["service"] = {
        "display_name": display_name,
        "page_title": page_title,
    }

    try:
        save_intelligence(
            service_id=service_id,
            primary_domain=primary,
            bundle_dict=bundle.to_dict(),
            is_first=previous is None,
        )
    except Exception:
        bundle.warnings.append("intelligence_save_failed")

    return bundle


def _should_probe_domain(source_url: str) -> bool:
    lower = source_url.lower()
    return lower.startswith("http")


def _internal_history_lines(bundle: WriterEnrichmentBundle) -> list[str]:
    internal = bundle.sections.get("internal", {})
    if not internal.get("prior_articles"):
        return []
    return [
        f"Platform history: {internal['prior_articles']} prior article(s); "
        f"{internal.get('latest_snapshot', '')}"
    ]


def _app_store_lines(bundle: WriterEnrichmentBundle) -> list[str]:
    apps = bundle.sections.get("app_stores", {})
    if apps.get("has_mobile_app_links"):
        return [f"Mobile app links detected: {', '.join(apps.get('stores_linked', []))}"]
    return ["Mobile app store links: none detected in crawl text"]


def _domain_probe_lines(bundle: WriterEnrichmentBundle) -> list[str]:
    probe = bundle.sections.get("domain_probe")
    if not probe:
        return []
    hint = probe.get("safety_hint", "unknown")
    https = probe.get("https", False)
    lines = [f"HTTPS surface check: {hint} (https={https})"]
    if probe.get("headers"):
        lines.append(f"Notable headers: {probe['headers']}")
    return lines


def _profile_diff_lines(bundle: WriterEnrichmentBundle) -> list[str]:
    pdiff = bundle.sections.get("profile_diff", {})
    if not (bundle.phase == "update" and pdiff.get("kind") == "update"):
        return []
    lines: list[str] = []
    if pdiff.get("domain_changed"):
        lines.append(
            f"⚠ Domain changed: {pdiff.get('previous_primary_domain')} → "
            f"{pdiff.get('current_primary_domain')}"
        )
    if pdiff.get("domains_added"):
        lines.append(f"New domains in copy: {', '.join(pdiff['domains_added'])}")
    lines.append(f"Text diff added lines: {pdiff.get('text_diff_lines', 0)}")
    return lines


def _chain_watch_lines(bundle: WriterEnrichmentBundle) -> list[str]:
    chain = bundle.sections.get("chain", {})
    if not chain.get("match_kind"):
        return []
    return [
        f"On-chain watch: {chain['match_kind']}={chain.get('match_value', '')} "
        f"({chain.get('tx_stats', 'pending')})"
    ]


def _social_post_lines(bundle: WriterEnrichmentBundle) -> list[str]:
    social = bundle.sections.get("social", {})
    lines: list[str] = []
    for post in social.get("linked_posts", []):
        if post.get("text"):
            author = post.get("author", "unknown")
            lines.append(f"Linked post (@{author}): {post['text'][:500]}")
            lines.append(f"  URL: {post.get('url', '')}")
        elif post.get("error"):
            lines.append(f"Linked post fetch failed: {post.get('url', '')} ({post['error']})")
    return lines


def _platform_search_lines(bundle: WriterEnrichmentBundle) -> list[str]:
    platform = bundle.sections.get("platform_search", {})
    if not platform.get("matches"):
        return []
    return [
        "Prior platform articles mentioning this service/domain: "
        + "; ".join(m["title"] for m in platform["matches"][:3])
    ]


def _scam_lines(bundle: WriterEnrichmentBundle) -> list[str]:
    if not bundle.sections.get("scam"):
        return []
    from app.modules.newspaper.scam_enrichment import ScamEnrichmentContext

    scam_sec = bundle.sections["scam"]
    return [
        "",
        format_scam_block(
            ScamEnrichmentContext(
                mentioned_urls=tuple(scam_sec.get("urls", [])),
                mentioned_domains=tuple(scam_sec.get("domains", [])),
                mentioned_algo_addresses=tuple(scam_sec.get("algo_addresses", [])),
                fetch_notes=tuple(scam_sec.get("notes", [])),
            )
        ),
    ]


def _warning_lines(bundle: WriterEnrichmentBundle) -> list[str]:
    if not bundle.warnings:
        return []
    return ["", "**Editor warnings:** " + "; ".join(bundle.warnings)]


def format_enrichment_for_writer(bundle: WriterEnrichmentBundle) -> str:
    """Markdown block appended to Mistral user prompt or template context."""
    lines = [f"## Writer enrichment ({bundle.phase})"]
    if bundle.primary_domain:
        lines.append(f"Primary domain: **{bundle.primary_domain}**")

    lines.extend(_internal_history_lines(bundle))
    lines.extend(_app_store_lines(bundle))
    lines.extend(_domain_probe_lines(bundle))
    lines.extend(_profile_diff_lines(bundle))
    lines.extend(_chain_watch_lines(bundle))
    lines.extend(_social_post_lines(bundle))
    lines.extend(_platform_search_lines(bundle))

    social = bundle.sections.get("social", {})
    lines.append(f"Social lane note: {social.get('note', '')}")
    whois = bundle.sections.get("whois", {})
    lines.append(f"Domain registration / owner: {whois.get('note', 'pending')}")

    lines.extend(_scam_lines(bundle))
    lines.extend(_warning_lines(bundle))

    lines.append(
        "\nUse enrichment as background only; do not invent facts not supported above "
        "or in the source crawl."
    )
    return "\n".join(lines)


def enrichment_block_for_row(
    row: object, payload: dict, topic: object, *, is_first_snapshot: bool
) -> str:
    """Gather + format the writer's enrichment block for one queued row, or "" when disabled or unavailable.

    The compose and edit paths ran byte-identical copies of this that differed
    only in `is_first_snapshot` (an edit is never a first snapshot), so it is a
    parameter rather than two functions.

    Best-effort by design: enrichment is extra context, never a precondition.
    Any collector failing, or the feature flag being off, degrades to composing
    without it rather than failing the article.
    """
    from app.core import config as worker_config

    try:
        if not worker_config.WRITER_ENRICHMENT_ENABLED:
            return ""
        bundle = gather_writer_enrichment(
            service_id=row.service_id,
            display_name=row.display_name,
            source_url=row.scrape_url,
            page_text=str(payload.get("page_text", "")),
            page_title=str(payload.get("page_title", "")),
            diff=payload.get("diff"),
            is_first_snapshot=is_first_snapshot,
            publish_topic=topic,
            match_kind=str(payload.get("match_kind", "")),
            match_value=str(payload.get("match_value", "")),
        )
        return format_enrichment_for_writer(bundle)
    except Exception:
        return ""
