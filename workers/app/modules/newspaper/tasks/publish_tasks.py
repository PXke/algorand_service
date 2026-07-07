from __future__ import annotations

import contextlib
import logging
import time

from app.celery_app import celery_app
from app.core.redis_lock import single_flight
from app.modules.newspaper.article_composer import compose_scrape_article
from app.modules.newspaper.article_store import insert_article
from app.modules.newspaper.article_tags import derive_article_tags
from app.modules.newspaper.ingest_signal import ingest_publish_signal
from app.modules.newspaper.publish_policy import PublishKind, PublishTier, PublishTopic
from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.scraper.core.factory import get_scraper_for_url
from app.modules.search.tasks.index_tasks import index_article, index_crawled_page

logger = logging.getLogger(__name__)


def _with_hero_image(body: str, og_image: str, alt: str, source_url: str = "") -> str:
    """Prepend the source's share image as a hero, if present and not already
    embedded. Real image from the page, never AI-generated."""
    if not og_image or og_image in body:
        return body
    if not og_image.lower().startswith(("http://", "https://")):
        return body
    if source_url:
        from app.modules.crawler.domain_tracker import domain_from_url

        image_domain = domain_from_url(og_image)
        site_domain = domain_from_url(source_url)
        if image_domain and site_domain and image_domain != site_domain:
            logger.warning(
                "dropping hero image from mismatched domain %s (source %s)",
                image_domain,
                site_domain,
            )
            return body
    safe_alt = (alt or "").replace("]", "").replace("[", "")
    return f"![{safe_alt}]({og_image})\n\n{body}"


def _merge_tags(base: list[str], extra) -> list[str]:
    out = list(base)
    for t in extra or ():
        if t and t not in out:
            out.append(t)
    return out[:10]


TRANSLATION_LANGS = ["zh", "hi", "es", "fr", "ar"]


def enqueue_article_translations(article_id: str) -> None:
    """Fan out translate_article tasks for an article that just became feed-
    visible. Publish-time only: most held drafts never pass review, so
    translating at held/recompose time burns 5 Mistral calls per dead draft."""
    try:
        from app.celery_app import celery_app

        for lang in TRANSLATION_LANGS:
            celery_app.send_task(
                "app.tasks.newspaper.translate_article", args=[str(article_id), lang]
            )
    except Exception:
        logger.warning("Failed to enqueue translation tasks", exc_info=True)


def _auto_merge_redirect(*, original_url: str, final_url: str, service_id: str) -> None:
    """A scrape that resolves to a DIFFERENT registrable domain than requested
    (a real HTTP redirect — e.g. algonode.io -> nodely.io after a rebrand) is
    definitionally the same website. Auto-fold the current service into
    whichever service already owns the resolved domain, rather than the two
    polling and composing independently forever (the nodely.io/algonode.io
    duplicate-article incident). Mirrors the admin domain-approval flow's
    "attach to the existing owner" behavior for brand-new domains, just
    triggered by a redirect discovered on an ALREADY-tracked service instead of
    a fresh frontier approval. Best-effort: never blocks the compose in
    progress. No-op when same domain, no existing owner, or already merged."""
    from app.modules.crawler.domain_tracker import domain_from_url
    from app.modules.newspaper.service_sources import merge_services, service_for_domain

    original_domain = domain_from_url(original_url)
    final_domain = domain_from_url(final_url)
    if not original_domain or not final_domain or original_domain == final_domain:
        return
    target_service = service_for_domain(final_domain)
    if not target_service or target_service == service_id:
        return
    with contextlib.suppress(Exception):
        merge_services(target_service_id=target_service, source_service_ids=[service_id])


def _compose_domain_for_row(row: QueuedPublishRow) -> str:
    """Registrable domain to count against the per-website daily article cap.
    Only web sources are capped (social pollers have their own pacing).

    Bluesky posts are ingested as a plain https://bsky.app/profile/.../post/...
    URL, which _source_kind_from_url can't tell apart from a real website — it
    would misclassify every monitored Bluesky account as sharing ONE "bsky.app"
    domain cap/cooldown (COMPOSE_MAX_PER_DOMAIN_PER_DAY / _HOURS), throttling
    unrelated accounts against each other. The stored payload source_kind is
    set once at ingest and is reliable, so check it first."""
    if row.payload.get("source_kind") == "bluesky":
        return ""
    if _source_kind_from_url(row.scrape_url) != "web":
        return ""
    from app.modules.crawler.domain_tracker import domain_from_url

    return domain_from_url(row.scrape_url)


def _gate_enforces_review(
    *, clf_decision: object, title: str, body: str, page_text: str, source_url: str
) -> bool:
    """Quality veto on the auto-publish path. True when a draft Classifier A
    would send STRAIGHT to the feed (``clf_decision is True``) should instead be
    diverted to human review because the deterministic gatekeeper fails.

    Honors ``GATEKEEPER_ENFORCE`` — default off, so this returns False (shadow
    mode, no behaviour change) until the quality head is trusted. Failure-tolerant:
    a None gate (disabled / error) never diverts."""
    from app.core import config

    if clf_decision is not True or not config.GATEKEEPER_ENFORCE:
        return False
    from app.modules.gatekeeper.live import gate_draft

    gate = gate_draft(
        source_text=page_text, article_text=f"{title}\n{body}", service_id=source_url
    )
    return gate is not None and not gate.passed


def _content_quality_fails(relevance: float, kind: PublishKind | None = None) -> bool:
    """Pre-compose veto: judge the actual context about to be handed to Mistral
    (page_text relevance, same 0-1 score_page scorer classify_pending_domains
    uses) so a poor-quality source never even reaches the writer.

    CONTENT_UPDATE gets its own, stricter CONTENT_UPDATE_QUALITY_FLOOR: a
    service is vetted once as a domain at discovery (lenient, first-crawl
    signal) but never re-checked per diff, so relevance can drift low over
    time without ever failing FRONTIER_CONTENT_REJECT_SCORE. This is a
    backstop behind the CONTENT_UPDATE_RELEVANCE_FLOOR enqueue-time gate
    (evaluate_enqueue), in case relevance shifts between ingest-time scoring
    and compose time. Other kinds keep sharing FRONTIER_CONTENT_REJECT_SCORE
    with the domain classifier."""
    from app.core import config

    if kind == PublishKind.CONTENT_UPDATE:
        return relevance < config.CONTENT_UPDATE_QUALITY_FLOOR
    return relevance < config.FRONTIER_CONTENT_REJECT_SCORE


def _quality_floor_fails(heuristic_grade: dict | None) -> bool:
    """Second quality veto on the auto-publish path: the writer's own two-stage
    grade/revise pass (article_grader.grade_article_draft, same score
    review_draft reports) falls below WRITER_QUALITY_FLOOR. Honors
    WRITER_QUALITY_GATE_ENABLED (default on). A missing/errored grade never
    diverts (fails open, matching _gate_enforces_review's failure-tolerant
    design)."""
    from app.core import config

    if not config.WRITER_QUALITY_GATE_ENABLED or not heuristic_grade:
        return False
    grade = heuristic_grade.get("grade")
    if grade is None:
        return False
    try:
        return float(grade) < config.WRITER_QUALITY_FLOOR
    except (TypeError, ValueError):
        return False


@single_flight(lambda *_a, **_kw: "compose:article", ttl=1860)
@single_flight(lambda row, **_kw: f"compose:{row.queue_id}", ttl=1800)
def publish_from_queued_row(
    row: QueuedPublishRow,
    *,
    publish_tier: PublishTier | None = None,
    enforce_domain_cap: bool = True,
) -> dict[str, str]:
    """Compose and insert one queue item (caller marks queue done).

    Two stacked single_flight locks:
    - ``compose:article`` (global): only ONE article composition runs across all
      workers at a time. The Mistral writer is an expensive agentic loop and
      several drains (breaking beat, standard drain on admin-accept, ensure-review)
      can fire at once; without this gate --concurrency=4 produces 4 full articles
      in parallel — "a lot in a very short time". A concurrent caller gets
      ``already_running`` and leaves its row pending for the next beat.
    - ``compose:{queue_id}`` (per-row): a row is never composed twice at once.

    The global ttl (1860s) matches the task hard time limit so a crashed compose
    frees the slot exactly when the worker would have been killed."""
    from app.modules.ai.mistral_client import MistralError
    from app.modules.newspaper.security import sanitize_body

    payload = row.payload
    publish_kind = PublishKind(row.publish_kind)
    try:
        topic = PublishTopic(row.topic)
    except ValueError:
        topic = PublishTopic.GENERIC
    mistral_only = bool(payload.get("mistral_only", False))
    tier_raw = publish_tier or payload.get("tier", PublishTier.STANDARD.value)
    try:
        tier = PublishTier(tier_raw) if isinstance(tier_raw, str) else tier_raw
    except ValueError:
        tier = PublishTier.STANDARD

    publish_mode = str(payload.get("publish_mode", "create"))
    linked_article_id = str(payload.get("linked_article_id", "")).strip()
    if publish_mode == "edit" and linked_article_id:
        from app.modules.newspaper.article_edit_service import run_article_edit

        return run_article_edit(row)

    # Per-website daily cap + 7-day diversity cooldown, both keyed on the
    # REGISTRABLE domain (domain_from_url collapses forum.folks.finance and
    # folks.finance -> folks.finance), so subdomains of one project share the cap
    # and cooldown. We ALWAYS resolve the domain so the compose is RECORDED below
    # (stamping the cooldown that spaces out the next article); enforce_domain_cap
    # only governs whether this row may be BLOCKED. Breaking alerts pass it False
    # so a critical warning is never held — but they must still stamp the cooldown,
    # otherwise the next same-domain story slips through the 7-day window.
    compose_domain = _compose_domain_for_row(row)
    if enforce_domain_cap and compose_domain:
        from app.modules.crawler.domain_tracker import domain_compose_cap_reached

        if domain_compose_cap_reached(compose_domain):
            return {"status": "domain_capped", "service_id": row.service_id}

    # Near-duplicate guard: if a very similar headline was published recently,
    # don't spend a Mistral compose on it. Runs HERE (composition) not at
    # enqueue, because more articles may have published since this was queued.
    from app.core import config as worker_config

    if worker_config.NOVELTY_GATE_ENABLED:
        from app.modules.newspaper.article_grader import recent_title_similarity

        sim, closest = recent_title_similarity(str(payload.get("page_title", "")))
        if sim >= worker_config.NOVELTY_MAX_SIMILARITY:
            # "duplicate" is a terminal outcome, so the drain dequeues the row.
            return {
                "status": "duplicate",
                "reason": "too_similar_to_recent",
                "service_id": row.service_id,
                "closest_title": closest,
                "similarity": round(sim, 2),
            }

    enrichment_block = ""
    try:
        from app.core import config as worker_config
        from app.modules.newspaper.writer_enrichment import (
            format_enrichment_for_writer,
            gather_writer_enrichment,
        )

        if worker_config.WRITER_ENRICHMENT_ENABLED:
            bundle = gather_writer_enrichment(
                service_id=row.service_id,
                display_name=row.display_name,
                source_url=row.scrape_url,
                page_text=str(payload.get("page_text", "")),
                page_title=str(payload.get("page_title", "")),
                diff=payload.get("diff"),
                is_first_snapshot=bool(payload.get("is_first_snapshot", False)),
                publish_topic=topic,
                match_kind=str(payload.get("match_kind", "")),
                match_value=str(payload.get("match_value", "")),
            )
            enrichment_block = format_enrichment_for_writer(bundle)
    except Exception:
        enrichment_block = ""

    # Classifier signals for the ACTUAL context about to be handed to Mistral
    # (page_text, same bundle compose_scrape_article receives — for web/service
    # sources this is the aggregated service_context, not just the raw page).
    # Computed once at ingest and carried on the payload; recompute only for
    # rows queued before signals existed. Read BEFORE composing so the content-
    # quality veto below can skip the Mistral call entirely, not just the feed.
    from app.modules.ai.content_signals import ContentSignals, compute_content_signals

    page_text_for_clf = str(payload.get("page_text", ""))
    signals = ContentSignals.from_payload(payload.get("signals")) or compute_content_signals(
        page_text_for_clf, row.scrape_url
    )
    clf_category = signals.category
    clf_decision, clf_confidence = signals.publish_decision, signals.confidence

    # Content-quality veto: judge the context BEFORE spending a Mistral call on it.
    if _content_quality_fails(signals.relevance, publish_kind):
        return {
            "status": "skipped",
            "reason": "poor_quality_content",
            "service_id": row.service_id,
            "relevance": round(signals.relevance, 3),
        }

    try:
        composed = compose_scrape_article(
            service_name=row.display_name,
            source_url=row.scrape_url,
            page_title=str(payload.get("page_title", "")),
            page_text=str(payload.get("page_text", "")),
            source_links=payload.get("inner_links") or [],
            txid=str(payload.get("txid", "")),
            round_num=int(payload.get("round_num", 0)),
            diff=payload.get("diff"),
            is_first_snapshot=bool(payload.get("is_first_snapshot", False)),
            publish_kind=publish_kind,
            publish_topic=topic,
            mistral_only=mistral_only,
            enrichment_block=enrichment_block,
            transcript_text=str(payload.get("transcript_text", "")),
            keywords=str(payload.get("keywords", "")),
            brief_id=str(payload.get("brief_id", "")),
        )
    except MistralError as exc:
        return {
            "status": "mistral_failed",
            "service_id": row.service_id,
            "detail": str(exc),
        }

    # Classifier gate: only confidently publish-worthy content goes straight
    # to the feed. Everything else is stored unpublished and queued for admin
    # review — approving the review item publishes the article.

    # Quality veto on the auto-publish path: a draft Classifier A would send
    # straight to the feed is diverted into the human-review path below when the
    # deterministic gatekeeper fails under GATEKEEPER_ENFORCE (default off).
    gate_enforced_review = _gate_enforces_review(
        clf_decision=clf_decision,
        title=composed.title,
        body=composed.body,
        page_text=page_text_for_clf,
        source_url=row.scrape_url,
    ) or _quality_floor_fails(getattr(composed, "heuristic_grade", None))

    # Resolve a hero/brand image when the upstream payload carried none, so both
    # the feed tile and the social/OG card show real artwork (best-effort). A
    # true share image (og/twitter) is also embedded in the body; a brand logo
    # populates image_url only (it's not a body banner).
    _payload_og = str(payload.get("og_image", "")).strip()
    hero_image = _payload_og
    image_field = _payload_og
    if not _payload_og:
        try:
            from app.modules.newspaper.source_image import resolve_source_images

            _og, _logo = resolve_source_images(
                source_url=row.scrape_url, service_id=row.service_id
            )
            hero_image = _og
            image_field = _og or _logo
        except Exception:
            logger.warning("failed to resolve source images for %s", row.scrape_url, exc_info=True)

    if clf_decision is not True or gate_enforced_review:
        from app.modules.crawler.classifier_review_store import (
            enqueue_classifier_review,
            has_pending_review_for_url,
        )
        from app.modules.newspaper.article_store import insert_stored_article

        if has_pending_review_for_url(row.scrape_url):
            return {
                "status": "duplicate_review_pending",
                "service_id": row.service_id,
            }
        from app.modules.crawler.classifier_review_store import review_queue_full

        if review_queue_full():
            return {"status": "review_queue_full", "service_id": row.service_id}

        held_kind = _source_kind_from_url(row.scrape_url)
        held_title, held_summary = composed.title, composed.summary
        held_tags = _merge_tags(
            derive_article_tags(
                service_id=row.service_id,
                source_kind=held_kind,
                title=held_title,
                publish_kind=composed.publish_kind or publish_kind.value,
                publish_topic=topic.value,
                publish_tier=tier.value,
            ),
            getattr(composed, "extra_tags", ()),
        )
        held_article_id, _ = insert_stored_article(
            service_id=row.service_id,
            title=held_title,
            summary=held_summary,
            body=_with_hero_image(
                sanitize_body(composed.body), hero_image, held_title, source_url=row.scrape_url
            ),
            trigger_txid=str(payload.get("txid", "")),
            trigger_round=int(payload.get("round_num", 0)),
            source_url=row.scrape_url,
            publish_to_feed=False,
            image_url=image_field,
            tags=held_tags,
            prompt_version=getattr(composed, "prompt_version", ""),
        )
        # Grade the draft so the human reviewer sees a quality score + reasons.
        grade_meta: dict[str, str] = {}
        try:
            import json as _json

            from app.modules.newspaper.article_grader import grade_article_draft

            grade = grade_article_draft(
                title=held_title,
                body=composed.body,
                source_url=row.scrape_url,
                published_at=str(payload.get("published_at", "")),
                tags=tuple(held_tags),
            )
            grade_meta = {
                "grade": str(grade["grade"]),
                "grade_detail": _json.dumps(
                    {"subscores": grade["subscores"], "issues": grade["issues"]},
                    separators=(",", ":"),
                ),
            }
        except Exception:
            grade_meta = {}
        # Deterministic gatekeeper: completeness + trace<->article numeric
        # entailment, surfaced to the reviewer. Shadow by default (computes and
        # annotates); GATEKEEPER_ENFORCE wires through for an auto-publish path.
        try:
            from app.modules.gatekeeper.live import gate_draft

            gate = gate_draft(
                source_text=page_text_for_clf,
                article_text=f"{held_title}\n{composed.body}",
                service_id=row.scrape_url,
            )
            if gate is not None:
                grade_meta.update(gate.as_metadata())
        except Exception:
            logger.warning("gatekeeper grading failed for %s", row.scrape_url, exc_info=True)
        review_id = enqueue_classifier_review(
            url=row.scrape_url,
            page_text=page_text_for_clf,
            page_title=str(payload.get("page_title", "")) or held_title,
            category=clf_category,
            storage_score=signals.storage_score,
            metadata={
                "article_id": held_article_id,
                "source": held_kind or "web",
                "confidence": f"{clf_confidence:.3f}",
                "categories": ",".join(signals.categories),
                "diverted_by": "gatekeeper" if gate_enforced_review else "classifier",
                **grade_meta,
            },
        )
        # A held-for-review draft is a created article — count it toward the
        # per-website daily cap so a domain can't exceed its COMPOSE_MAX_PER_DOMAIN_PER_DAY.
        if compose_domain:
            from app.modules.crawler.domain_tracker import record_domain_compose

            record_domain_compose(compose_domain)
        if row.service_id:
            from app.modules.crawler.domain_tracker import record_service_compose

            record_service_compose(row.service_id)
        if payload.get("source_kind") == "editorial_assignment":
            from app.modules.newspaper.editorial_assignment import mark_brief_run

            with contextlib.suppress(Exception):
                mark_brief_run(
                    brief_id=str(payload.get("brief_id", "")), article_id=held_article_id
                )
    return {
        "status": "review",
            "service_id": row.service_id,
            "article_id": held_article_id,
            "review_id": review_id,
        }

    from app.modules.newspaper.publish_daily_guard import (
        PublishCapExceededError,
        assert_publish_allowed,
        release_publish_slot,
        reserve_publish_slot,
    )

    try:
        assert_publish_allowed(tier=tier)
        reserved, reserve_reason = reserve_publish_slot(tier=tier)
        if not reserved:
            return {"status": "rate_limited", "reason": reserve_reason, "tier": tier.value}
    except PublishCapExceededError as exc:
        return {"status": "rate_limited", "reason": str(exc), "tier": tier.value}

    title, summary, body = composed.title, composed.summary, sanitize_body(composed.body)
    body = _with_hero_image(body, hero_image, title, source_url=row.scrape_url)
    if tier == PublishTier.BREAKING and not title.lower().startswith("breaking"):
        title = f"Breaking: {title}"
        summary = f"**Breaking news.** {summary}"
    source_kind = _source_kind_from_url(row.scrape_url)
    try:
        article_id = insert_article(
            service_id=row.service_id,
            title=title,
            summary=summary,
            body=body,
            trigger_txid=str(payload.get("txid", "")),
            trigger_round=int(payload.get("round_num", 0)),
            source_url=row.scrape_url,
            image_url=image_field,
            tags=_merge_tags(
                derive_article_tags(
                    service_id=row.service_id,
                    source_kind=source_kind,
                    title=title,
                    publish_kind=composed.publish_kind or publish_kind.value,
                    publish_topic=topic.value,
                    publish_tier=tier.value,
                ),
                getattr(composed, "extra_tags", ()),
            ),
            prompt_version=getattr(composed, "prompt_version", ""),
        )
    except Exception:
        release_publish_slot(tier=tier)
        raise
    index_article.delay(
        article_id=article_id,
        title=title,
        summary=summary,
        body=body,
        service_id=row.service_id,
        published_at_epoch=int(time.time()),
    )
    # Notify IndexNow (Bing/Ecosia/DuckDuckGo, Yandex, Seznam, Naver) so the new
    # story gets crawled in minutes. Best-effort — never let it block a publish.
    try:
        from app.modules.newspaper.indexnow import article_url, ping

        ping([article_url(article_id)])
    except Exception:
        logger.warning("IndexNow ping failed for article %s", article_id, exc_info=True)
    page_text = str(payload.get("page_text", ""))
    page_title = str(payload.get("page_title", ""))
    if page_text:
        index_crawled_page.delay(
            url=row.scrape_url,
            title=page_title,
            text=page_text,
            service_id=row.service_id,
        )
    publish_mode = str(payload.get("publish_mode", "create"))
    if publish_mode == "create":
        from app.modules.newspaper.article_matching import (
            build_match_keys,
            register_article_match_keys,
        )

        keys = payload.get("match_keys")
        if not isinstance(keys, list) or not keys:
            keys = build_match_keys(
                service_id=row.service_id,
                page_text=str(payload.get("page_text", "")),
                source_url=row.scrape_url,
                extra_keywords=("scam",) if topic == PublishTopic.SCAM_ALERT else (),
                match_kind=str(payload.get("match_kind", "")),
                match_value=str(payload.get("match_value", "")),
            )
        else:
            keys = [
                (str(k[0]), str(k[1]))
                for k in keys
                if isinstance(k, (list, tuple)) and len(k) == 2
            ]
        register_article_match_keys(article_id=article_id, keys=keys)

    # Published straight to the feed is a created article — count it toward the
    # per-website daily cap.
    if compose_domain:
        from app.modules.crawler.domain_tracker import record_domain_compose

        record_domain_compose(compose_domain)
    if row.service_id:
        from app.modules.crawler.domain_tracker import record_service_compose

        record_service_compose(row.service_id)

    if payload.get("source_kind") == "editorial_assignment":
        from app.modules.newspaper.editorial_assignment import mark_brief_run

        with contextlib.suppress(Exception):
            mark_brief_run(brief_id=str(payload.get("brief_id", "")), article_id=article_id)

    enqueue_article_translations(str(article_id))

    return {
        "status": "published",
        "article_id": article_id,
        "composer": composed.composer,
        "publish_kind": publish_kind.value,
        "topic": topic.value,
        "tier": tier.value,
        "publish_mode": publish_mode,
        "linked_article_id": str(payload.get("linked_article_id", "")),
    }


def run_publish_pipeline(
    *,
    service_id: str,
    display_name: str,
    scrape_url: str,
    match_kind: str,
    match_value: str,
    txid: str,
    round_num: int,
    mistral_only: bool = False,
) -> dict[str, str]:
    """Scrape source and enqueue via shared ingest path."""
    from app.core import config as worker_config
    from app.modules.newspaper.publish_daily_guard import is_standard_publish_saturated
    from app.modules.scraper.core.scrape_cooldown import (
        clear_scrape_cooldown,
        is_on_cooldown,
        record_scrape_failure,
    )

    if worker_config.CRAWL_PAUSE_WHEN_PUBLISH_CAP_FULL and is_standard_publish_saturated():
        return {
            "status": "skipped",
            "reason": "daily_publish_cap_saturated_crawl_paused",
            "txid": txid,
        }

    on_cooldown, reason = is_on_cooldown(service_id)
    if on_cooldown:
        return {"status": "skipped", "reason": reason, "txid": txid}

    from app.modules.scraper.crawler_registry import crawl_disabled_reason

    disabled = crawl_disabled_reason(scrape_url)
    if disabled:
        return {"status": "skipped", "reason": disabled, "txid": txid}

    # Poor-quality source gate: a domain the content-relevance classifier (or an
    # admin) has already marked not-relevant must not keep composing just
    # because it was approved before that verdict existed. Skip before the
    # scrape so we don't pay for fetching it either. Web-only — chain/mail
    # sources aren't domain-scored.
    lower_scrape_url = scrape_url.lower()
    if lower_scrape_url.startswith(("http://", "https://", "browser://")):
        from app.modules.crawler.domain_tracker import domain_from_url, is_dead_end_domain

        early_domain = domain_from_url(scrape_url)
        if early_domain and is_dead_end_domain(early_domain):
            return {"status": "skipped", "reason": "poor_quality_source", "txid": txid}

    scraper = get_scraper_for_url(scrape_url)
    try:
        result = scraper.scrape(url=scrape_url, source_id=service_id)
    except Exception:
        record_scrape_failure(service_id)
        raise
    # Success resets the exponential backoff streak for this source.
    clear_scrape_cooldown(service_id)
    if lower_scrape_url.startswith(("http://", "https://", "browser://")):
        _auto_merge_redirect(
            original_url=scrape_url,
            final_url=getattr(result, "url", "") or scrape_url,
            service_id=service_id,
        )
    # Recency gate: a page whose own publish date is older than the window is
    # low-value to report on now — skip before composing. No date => not gated.
    if worker_config.RECENCY_GATE_ENABLED:
        from app.modules.scraper.core.page_metadata import is_stale_page

        if is_stale_page(result.published_at, worker_config.PAGE_STALE_MAX_AGE_DAYS):
            return {
                "status": "skipped",
                "reason": "stale_page",
                "published_at": result.published_at,
                "txid": txid,
            }
    source_kind = _source_kind_from_url(scrape_url)
    # Per-domain daily article cap (web sources): a churning page must not be
    # re-composed every poll. Early-out here to avoid enqueuing a candidate the
    # compose stage would reject anyway. The count itself is incremented when an
    # article is actually created (see publish_from_queued_row), so this read is
    # purely an optimization.
    if source_kind == "web":
        from app.modules.crawler.domain_tracker import (
            domain_compose_cap_reached,
            domain_from_url,
        )

        compose_domain = domain_from_url(scrape_url)
        if compose_domain and domain_compose_cap_reached(compose_domain):
            return {"status": "skipped", "reason": "domain_daily_compose_cap", "txid": txid}
    if source_kind == "web":
        try:
            from app.modules.scraper.core.link_extractor import enqueue_page_links

            enqueue_page_links(raw_html=result.raw_html, page_url=scrape_url, source="web")
        except Exception:
            logger.warning("failed to enqueue page links from %s", scrape_url, exc_info=True)

    # Service-watch aggregate: for web services the snapshot/diff/compose unit
    # is the service's aggregated recent pages (all its domains), never just
    # this one URL. Falls back to the single page when there is no harvest yet
    # or aggregation fails. Also re-queues the aggregate's pages for a fresh
    # crawl so next week's aggregate reflects current content.
    page_text = result.text
    if source_kind == "web" and worker_config.SERVICE_CONTEXT_ENABLED:
        try:
            from app.modules.newspaper.service_context import (
                build_service_context,
                refresh_service_pages,
            )

            page_text = build_service_context(
                service_id=service_id,
                display_name=display_name,
                entry_url=scrape_url,
                entry_title=result.title,
                entry_text=result.text,
            )
            refresh_service_pages(service_id, entry_url=scrape_url)
        except Exception:
            logger.warning(
                "service context aggregation failed for %s — using entry page only",
                service_id,
                exc_info=True,
            )
            page_text = result.text

    outcome = ingest_publish_signal(
        service_id=service_id,
        display_name=display_name,
        source_url=scrape_url,
        page_title=result.title,
        page_text=page_text,
        source_kind=source_kind,
        match_kind=match_kind,
        match_value=match_value,
        txid=txid,
        round_num=round_num,
        mistral_only=mistral_only,
        og_image=getattr(result, "og_image", ""),
        published_at=getattr(result, "published_at", ""),
        inner_links=getattr(result, "links", None),
    )
    return outcome


@celery_app.task(name="app.tasks.newspaper.publish_from_chain_event")
def publish_from_chain_event(
    *,
    service_id: str,
    display_name: str,
    scrape_url: str,
    match_kind: str,
    match_value: str,
    txid: str,
    round_num: int,
) -> dict[str, str]:
    if not scrape_url:
        return {"status": "skipped", "reason": "no_scrape_url"}
    return run_publish_pipeline(
        service_id=service_id,
        display_name=display_name,
        scrape_url=scrape_url,
        match_kind=match_kind,
        match_value=match_value,
        txid=txid,
        round_num=round_num,
    )


def _source_kind_from_url(scrape_url: str) -> str | None:
    lower = scrape_url.lower()
    if lower.startswith("youtube://") or lower.startswith("youtube:"):
        return "youtube"
    if lower.startswith("mail://"):
        return "mail"
    # browser:// is an SPA web source (Playwright). It MUST classify as web so the
    # per-domain daily cap + diversity cooldown apply — otherwise SPA sources
    # silently bypass both and can republish without spacing.
    if (
        lower.startswith("http://")
        or lower.startswith("https://")
        or lower.startswith("browser://")
    ):
        return "web"
    return "chain_only"


@celery_app.task(name="app.tasks.newspaper.recompose_review")
def recompose_review(review_id: str) -> dict[str, str]:
    """Re-run composition on a pending review's stored source and REPLACE the
    review with a fresh proposal. Lets an admin watch a previously bad article
    improve as the writer/grader evolve, without waiting for the source to
    change. This is a deliberate manual replay, so it bypasses the dedup /
    novelty / domain gates the normal pipeline applies."""
    import json as _json
    from uuid import UUID

    from app.core.cassandra import get_cassandra_session
    from app.modules.ai.mistral_client import MistralError
    from app.modules.crawler.classifier_review_store import (
        complete_classifier_review,
        enqueue_classifier_review,
    )
    from app.modules.newspaper.article_store import insert_stored_article
    from app.modules.newspaper.security import sanitize_body

    try:
        rid = UUID(review_id)
    except ValueError:
        return {"status": "error", "reason": "bad_review_id"}

    from app.core.statements import ClassifierReviewStmts

    session = get_cassandra_session()
    row = session.execute(ClassifierReviewStmts.GET_FOR_PUBLISH, (rid,)).one()
    if row is None:
        return {"status": "error", "reason": "review_not_found"}

    url = row.url or ""
    page_text = row.page_text or ""
    page_title = row.page_title or ""
    # Carry forward service_id / hero image from the prior proposal's metadata.
    old: dict = {}
    raw = dict(row.metadata or {}).get("raw")
    if raw:
        try:
            old = _json.loads(raw)
        except (ValueError, TypeError):
            old = {}
    service_id = str(old.get("service_id") or url)
    og_image = str(old.get("og_image") or "")
    old_article_id = str(old.get("article_id") or "")
    category = row.category or ""
    storage_score = float(row.storage_score or 0)
    kind = _source_kind_from_url(url)

    # Free the review slot NOW so clicking Recompose empties the queue immediately,
    # rather than leaving the stale item for the minutes-long writer loop. The
    # fresh proposal is enqueued below when compose finishes.
    complete_classifier_review(review_id, resolution="recomposing")

    try:
        composed = compose_scrape_article(
            service_name=url,
            source_url=url,
            page_title=page_title,
            page_text=page_text,
            txid=f"recompose-{review_id[:12]}",
            round_num=0,
            diff=None,
            is_first_snapshot=True,
            publish_kind=PublishKind.SERVICE_DISCOVERY,
            publish_topic=PublishTopic.GENERIC,
        )
    except MistralError as exc:
        # Compose failed — restore the original proposal so the review isn't lost.
        enqueue_classifier_review(
            url=url,
            page_text=page_text,
            page_title=page_title,
            category=category,
            storage_score=storage_score,
            metadata={
                "article_id": old_article_id,
                "source": kind or "web",
                "recompose_failed": str(exc)[:200],
            },
        )
        return {"status": "mistral_failed", "detail": str(exc)[:200]}
    tags = _merge_tags(
        derive_article_tags(
            service_id=service_id,
            source_kind=kind,
            title=composed.title,
            publish_kind=composed.publish_kind or PublishKind.SERVICE_DISCOVERY.value,
            publish_topic=PublishTopic.GENERIC.value,
            publish_tier=PublishTier.STANDARD.value,
        ),
        getattr(composed, "extra_tags", ()),
    )
    article_id, _ = insert_stored_article(
        service_id=service_id,
        title=composed.title,
        summary=composed.summary,
        body=_with_hero_image(
            sanitize_body(composed.body), og_image, composed.title, source_url=url
        ),
        trigger_txid=f"recompose-{review_id[:12]}",
        trigger_round=0,
        source_url=url,
        publish_to_feed=False,
        image_url=og_image,
        tags=tags,
        prompt_version=getattr(composed, "prompt_version", ""),
    )

    # Grade + deterministic gate, mirroring publish_from_queued_row so the
    # reviewer sees a fresh score and reasons next to the new draft.
    grade_meta: dict[str, str] = {}
    try:
        from app.modules.newspaper.article_grader import grade_article_draft

        grade = grade_article_draft(title=composed.title, body=composed.body, source_url=url)
        grade_meta = {
            "grade": str(grade["grade"]),
            "grade_detail": _json.dumps(
                {"subscores": grade["subscores"], "issues": grade["issues"]},
                separators=(",", ":"),
            ),
        }
    except Exception:
        grade_meta = {}
    try:
        from app.modules.gatekeeper.live import gate_draft

        gate = gate_draft(
            source_text=page_text,
            article_text=f"{composed.title}\n{composed.body}",
            service_id=url,
        )
        if gate is not None:
            grade_meta.update(gate.as_metadata())
    except Exception:
        logger.warning("gatekeeper grading failed for %s", url, exc_info=True)

    # The old review was already completed (slot freed on click); enqueue the
    # fresh proposal for the same URL so it lands in the queue the admin watches.
    new_review_id = enqueue_classifier_review(
        url=url,
        page_text=page_text,
        page_title=page_title or composed.title,
        category=category,
        storage_score=storage_score,
        metadata={
            "article_id": article_id,
            "source": kind or "web",
            "recomposed_from": review_id,
            **grade_meta,
        },
    )

    return {"status": "ok", "review_id": new_review_id, "article_id": article_id}


@celery_app.task(name="app.tasks.newspaper.assign_editorial_brief")
def assign_editorial_brief(brief_id: str) -> dict[str, str]:
    """First-run assignment for an active editorial brief — thin wrapper, see
    app.modules.newspaper.editorial_assignment for the actual logic."""
    from app.modules.newspaper.editorial_assignment import assign_editorial_brief as _assign

    return _assign(brief_id)


@celery_app.task(name="app.tasks.newspaper.refresh_editorial_brief")
def refresh_editorial_brief(brief_id: str) -> dict[str, str]:
    """Cadence refresh for an editorial brief's linked article — thin wrapper,
    see app.modules.newspaper.editorial_assignment for the actual logic."""
    from app.modules.newspaper.editorial_assignment import refresh_editorial_brief as _refresh

    return _refresh(brief_id)


@celery_app.task(name="app.tasks.newspaper.scan_editorial_brief_schedule")
def scan_editorial_brief_schedule() -> dict[str, object]:
    """Safety-net beat: assign any active brief still missing its first article,
    and refresh any brief whose cadence has elapsed."""
    from app.modules.newspaper.editorial_assignment import (
        scan_editorial_brief_schedule as _scan,
    )

    return _scan()

@celery_app.task(name="app.tasks.newspaper.translate_article")
def translate_article_task(
    article_id: str,
    lang: str,
    english_title: str = "",
    english_summary: str = "",
    english_body: str = "",
) -> dict[str, str]:
    """Background task to translate an article into a target language using the LLM.

    Reads the CURRENT article text from the store rather than trusting enqueue-time
    args (kept only for in-flight compatibility with pre-2026-07-05 enqueues), so a
    recompose between enqueue and run can't persist a stale translation. Skips
    languages already stored — re-enqueueing is free."""
    import json

    from app.modules.ai.mistral_compose import translate_article_mistral
    from app.modules.newspaper.article_store import get_article, update_article_translations

    try:
        article = get_article(article_id)
        if article is None or not (article.body or "").strip():
            return {"status": "error", "reason": "article_not_found_or_empty"}
        if lang in (article.translations or {}):
            return {"status": "skipped", "reason": "already_translated", "lang": lang}

        translated = translate_article_mistral(
            english_title=article.title or "",
            english_summary=article.summary or "",
            english_body=article.body or "",
            target_language=lang,
        )

        # Store as JSON in the Cassandra map
        translations = {lang: json.dumps(translated, ensure_ascii=False)}

        update_article_translations(article_id, translations)
        return {"status": "ok", "article_id": article_id, "lang": lang}
    except Exception as e:
        logger.error(f"Failed to translate article {article_id} to {lang}: {e}")
        return {"status": "error", "reason": str(e)}
