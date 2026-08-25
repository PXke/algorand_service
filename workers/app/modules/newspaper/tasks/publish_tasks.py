"""Celery tasks and helpers that compose and publish a queued row."""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from celery import Task

    from app.modules.ai.content_signals import ContentSignals
    from app.modules.newspaper.article_store import ArticleDetail
    from app.modules.newspaper.editorial_assignment import EditorialBrief

from app.celery_app import celery_app
from app.core import config as worker_config
from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS
from app.core.redis_lock import single_flight
from app.modules.newspaper.article_composer import ArticleComposeResult, compose_scrape_article
from app.modules.newspaper.article_store import insert_article
from app.modules.newspaper.article_tags import derive_article_tags, order_reader_tags
from app.modules.newspaper.compose_lock import COMPOSE_LOCK_KEY, ComposeBusyError
from app.modules.newspaper.ingest_signal import ingest_publish_signal
from app.modules.newspaper.publish_policy import PublishKind, PublishTier, PublishTopic
from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.newspaper.writer_enrichment import enrichment_block_for_row
from app.modules.scraper.core.factory import get_scraper_for_url
from app.modules.search.tasks.index_tasks import index_article, index_crawled_page

logger = logging.getLogger(__name__)


# Same identifying UA source_image.py sends for the page fetch — the raw
# asset fetch here was still going out with none, and some of the same
# Cloudflare/WAF-fronted hosts that block a bare page fetch also block a bare
# image fetch (2026-08-25).
_HERO_FETCH_UA = "algorand-platform-newspaper/1.0 (+https://algorand.pxke.me)"

# Host fragments that mark a third-party og:image as CDN/media hosting rather
# than another site's content. Share images hosted off-domain are the NORM
# (cloudinary, cloudfront, discourse-cdn, ipfs gateways…) — only a third-party
# host that looks like a different *website* (the cryptonews.net page serving a
# cnews24.ru stock photo) should sink the hero.
_IMAGE_CDN_HINTS: tuple[str, ...] = (
    "cdn",
    "img",
    "image",
    "static",
    "media",
    "assets",
    "ipfs",
    "cloudfront",
    "cloudinary",
    "imgix",
    "akamai",
    "fastly",
    "amazonaws",
    "googleusercontent",
    "twimg",
    # A docs site's own OG-image-generator subdomain (e.g. defly.gitbook.io
    # serving ~gitbook/ogimage/… for docs.defly.app) — same shared-host
    # pattern as the others here, found missing during the 2026-07-14
    # backfill (a perfectly good 1200x630 GitBook OG image was rejected).
    "gitbook",
    # Medium's own media CDN (miro.medium.com) — the _PLATFORM_SUFFIXES
    # same-platform-subdomain rule below only fires when the SOURCE itself is
    # on a *.medium.com host; a Medium publication on its own custom domain
    # (blog.perawallet.app, a real Medium blog with a vanity domain) has a
    # site_domain of perawallet.app, so that rule never triggers and every
    # og:image — always served from miro.medium.com regardless of the
    # publication's domain — was rejected as a foreign host. Root-caused
    # 2026-08-25 against a real imageless blog.perawallet.app article.
    "miro",
)


def _plausible_image_host(og_image: str, source_url: str) -> bool:
    """True when the og:image host plausibly belongs to the source: same registrable domain, or a recognizable CDN/media host."""
    from urllib.parse import urlparse

    from app.modules.crawler.domain_tracker import _PLATFORM_SUFFIXES, domain_from_url

    # Synthetic, non-fetchable source identifiers (editorial://brief/…,
    # mail://message/…) have no real site to compare an image's domain
    # against. domain_from_url parses the netloc of ANY scheme://netloc/...
    # string, so "editorial://brief/<uuid>" reads as if "brief" were a real
    # hostname — a URL-parsing artifact, not a genuine site — which silently
    # rejected perfectly good images (algorand.co, GitBook OG images, …) on
    # these lanes the first time a re-validation backfill ever exercised this
    # combination (2026-07-14). Only http(s) sources have a real domain to
    # check at all.
    if not source_url.lower().startswith(("http://", "https://")):
        return True

    image_domain = domain_from_url(og_image)
    site_domain = domain_from_url(source_url)
    if not image_domain or not site_domain or image_domain == site_domain:
        return True
    # domain_from_url deliberately keeps multi-tenant platform subdomains
    # distinct (valar-staking.medium.com != some-other-author.medium.com) so
    # unrelated authors aren't merged into one "source" — but that same rule
    # makes a platform's own SHARED media host (miro.medium.com serves images
    # for every *.medium.com publication) look foreign to any one publication.
    # Found live 2026-07-13: every Medium-sourced article was silently losing
    # its hero image because of exactly this. Treat same-platform subdomains
    # as plausible for each other.
    image_suffix = ".".join(image_domain.split(".")[-2:])
    site_suffix = ".".join(site_domain.split(".")[-2:])
    if image_suffix == site_suffix and image_suffix in _PLATFORM_SUFFIXES:
        return True
    host = (urlparse(og_image).hostname or "").lower()
    return any(hint in host for hint in _IMAGE_CDN_HINTS)


def _is_real_image(url: str, *, min_dimension: int = 120) -> bool:
    """Fetch and decode a candidate image; reject content that's too small or blank to be worth showing.

    This is a QUALITY judgment made from the actual pixels, not a URL-shape
    guess (that's what the frontend's looksLikeLogoUrl does, since it can't
    fetch/decode client-side — the backend can, so it shouldn't need to
    guess). min_dimension=120 draws the line between a real favicon (16-48px,
    genuinely too small/blurry blown up) and a decent app/touch icon (120px+,
    e.g. AlgoVanity's 192x192 apple-touch-icon — perfectly fine as a feed
    thumbnail even though its URL "looks like" a logo). Also rejects
    anti-hotlink decoy pixels — some sites (Cloudflare-fronted or otherwise)
    serve a valid-but-blank 1x1 transparent image to non-browser requests
    instead of an error, so it looks like a successful fetch while rendering
    as nothing (root-caused via geographia.com.br and docs.vestigelabs.org,
    2026-07-14).

    One retry on any failure (network hiccup, timeout, decode error) before
    giving up: a backfill re-validating many articles hits dozens of
    different external hosts back-to-back, and a single transient blip must
    not permanently blank out a genuinely good image — this fails closed
    (rejects) on the FIRST error only after a second attempt also fails,
    which happened for real to several perfectly fine images
    (algorand.co, two GitBook OG images, x402.org, hesab.com) during the
    2026-07-14 backfill and had to be manually restored.

    SVG icons are a separate case, not a retry-worthy failure: PIL has no SVG
    decoder at all, so Image.open() raises UnidentifiedImageError on every
    single SVG regardless of content — root-caused 2026-08-25 chasing a real
    imageless article whose brand icon was a genuine, correctly-served
    favicon.svg (reproduced against vitejs.dev, vuejs.org and GitHub's own
    favicon.svg: all three real, all three unconditionally rejected as
    "degenerate/decoy"). A vector image has no pixel dimensions to be too
    small or blurry, so it skips the min_dimension/blank-alpha checks
    entirely and is accepted on nothing more than a sane byte size and an
    actual `<svg` tag — guarding against a mislabeled 404/redirect landing
    page served at a `.svg` path with the real content-type of text/html.
    """
    import time
    from io import BytesIO

    from app.core.net_guard import guarded_get

    last_exc: Exception | None = None
    for attempt in range(2):
        if attempt:
            time.sleep(0.75)
        try:
            resp = guarded_get(url, headers={"User-Agent": _HERO_FETCH_UA}, timeout=10.0)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            if "svg" in content_type or url.lower().split("?")[0].endswith(".svg"):
                body = resp.content
                return len(body) > 64 and b"<svg" in body[:1024].lower()
            from PIL import Image

            img = Image.open(BytesIO(resp.content))
            if img.width < min_dimension or img.height < min_dimension:
                return False
            if img.mode in ("RGBA", "LA"):
                alpha = img.convert("RGBA").split()[-1]
                if alpha.getextrema() == (0, 0):  # fully transparent
                    return False
            return True
        except Exception as exc:
            last_exc = exc
    logger.info("hero image validation fetch failed for %s (after retry): %s", url, last_exc)
    return False


def _validated_hero(image: str, source_url: str) -> str:
    """Gate a candidate share image the same way _with_hero_image gates the body embed — previously only the body used this check, so a template site's stale/foreign og:image (e.g. copy-pasted from an unrelated project) could still become the article's image_url/feed-tile/OG-card even though it was correctly kept out of the body text.

    Domain-plausibility only (no network I/O, no URL-shape guessing) — see
    _validated_hero_checked for the actual content-quality check (real
    dimensions, not-blank) used at compose call sites. A URL that merely
    "looks like" a logo (e.g. contains apple-touch/icon/favicon) is NOT
    rejected here: whether it's actually too small/blurry to use is a
    pixel-level judgment, not a URL-shape one — see _is_real_image.
    """
    if not image:
        return image
    if source_url and not _plausible_image_host(image, source_url):
        logger.warning("dropping implausible og:image %s for %s", image, source_url)
        return ""
    return image


# A real declared share image can be a modest photo and still look fine as a
# hero; a bare brand icon (favicon / apple-touch-icon, no og:image anywhere)
# needs to be genuinely substantial before it's fit to stand in for one full-
# width — a barely-over-120px icon still reads as blurry/pixelated at hero
# size, even though it's "real" by the og:image bar.
_LOGO_MIN_DIMENSION = 256


def _validated_hero_checked(image: str, source_url: str, kind: str = "og") -> str:
    """_validated_hero, then fetch+decode to reject images too small or blank to be worth showing (see _is_real_image). Does real network I/O — use this at compose call sites, not _validated_hero directly, so pure unit tests of the URL-based gate stay fast and deterministic. ``kind="logo"`` applies the stricter _LOGO_MIN_DIMENSION floor instead of _is_real_image's default."""
    image = _validated_hero(image, source_url)
    min_dimension = _LOGO_MIN_DIMENSION if kind == "logo" else 120
    if image and not _is_real_image(image, min_dimension=min_dimension):
        logger.warning("dropping degenerate/decoy %s image %s for %s", kind, image, source_url)
        return ""
    return image


def _with_hero_image(body: str, og_image: str, alt: str, source_url: str = "") -> str:
    """Prepend the source's share image as a hero, if present and not already embedded. Real image from the page, never AI-generated."""
    if not og_image or og_image in body:
        return body
    if not og_image.lower().startswith(("http://", "https://")):
        return body
    if source_url and not _plausible_image_host(og_image, source_url):
        logger.warning(
            "dropping hero image from implausible host %s (source %s)",
            og_image,
            source_url,
        )
        return body
    safe_alt = (alt or "").replace("]", "").replace("[", "")
    # og_image is the raw URL scraped from the source page's share-image meta
    # tag -- sites routinely publish these with literal spaces or other
    # unencoded characters in the path (found live 2026-08-09:
    # ".../SCxj-Build on Algorand Course.png" broke the hero image at the
    # very top of an article). A bare markdown link destination can't contain
    # unescaped whitespace per CommonMark, so the parser truncates the URL at
    # the first space and spills the rest as literal text right after the
    # image. quote() with a safe set covering URL-structural characters
    # re-encodes only what's actually unsafe, without double-encoding any
    # %XX sequences the URL may already have.
    from urllib.parse import quote

    safe_og_image = quote(og_image, safe="!#$%&'()*+,/:;=?@[]~")
    return f"![{safe_alt}]({safe_og_image})\n\n{body}"


def _merge_tags(base: list[str], extra: list[str] | None) -> list[str]:
    out = list(base)
    for t in extra or ():
        if t and t not in out:
            out.append(t)
    return order_reader_tags(out)[:10]


def _stash_capped_compose_to_backlog(
    *,
    row: QueuedPublishRow,
    composed: ArticleComposeResult,
    payload: dict[str, Any],
    hero_image: str | None,
    image_field: str | None,
    publish_kind: PublishKind,
    topic: PublishTopic,
    tier: PublishTier,
    reason: str,
) -> dict[str, str]:
    """The daily cap filled between the drain's pre-compose check and this publish attempt (composes take minutes; another lane can take the last slot meanwhile). The old behavior returned rate_limited and THREW AWAY the finished article — the 2026-07-15 'Seven Real-World Apps' YouTube compose (~300k tokens, status ok) died exactly this way, then its queue row aged out and the content was lost. Store it unlisted with status='backlog' instead, exactly like the auto-approve backlog path: the paced release ships it once a slot opens."""
    from datetime import UTC, datetime

    from app.modules.newspaper.article_store import insert_stored_article
    from app.modules.newspaper.security import sanitize_body

    title, summary = composed.title, composed.summary
    body = _with_hero_image(
        sanitize_body(composed.body), hero_image, title, source_url=row.scrape_url
    )
    tags = _merge_tags(
        derive_article_tags(
            service_id=row.service_id,
            source_kind=_source_kind_from_url(row.scrape_url),
            title=title,
            publish_kind=composed.publish_kind or publish_kind.value,
            publish_topic=_effective_alert_topic(topic, composed).value,
            publish_tier=tier.value,
        ),
        getattr(composed, "extra_tags", ()),
    )
    approved_at = datetime.now(tz=UTC)
    article_id, _ = insert_stored_article(
        service_id=row.service_id,
        title=title,
        summary=summary,
        body=body,
        trigger_txid=str(payload.get("txid", "")),
        trigger_round=int(payload.get("round_num", 0)),
        source_url=row.scrape_url,
        publish_to_feed=False,
        status="backlog",
        image_url=image_field,
        tags=tags,
        prompt_version=getattr(composed, "prompt_version", ""),
        interest_score=0.0,  # interest unknown here; FIFO within the day is fine
        approved_at=approved_at,
    )
    logger.warning(
        "daily cap filled mid-compose (%s) — stored article %s to the "
        "pending_feed backlog instead of discarding the finished compose",
        reason,
        article_id,
    )
    return {
        "status": "approved_backlog",
        "service_id": row.service_id,
        "article_id": article_id,
        "reason": reason,
    }


def enqueue_missing_article_translations(article_id: str) -> int:
    """Enqueue ONE translate_article_batch task covering every lang not yet stored. Returns count queued (languages, not tasks -- callers key off this number, not "how many Celery tasks fired").

    One task, not one per language: local translation loads a multi-GB model
    per engine, so batching by article lets the batch task load each engine
    at most once and reuse it for every language routed there, instead of a
    fresh worker process potentially loading BOTH engines across an
    unpredictable spread of independently-scheduled tasks (see
    local_translate.translate_article_batch for the actual grouping/load/
    unload logic).
    """
    try:
        from app.celery_app import celery_app
        from app.modules.newspaper.article_store import get_article

        article = get_article(article_id)
        if article is None or not (article.body or "").strip():
            return 0
        existing = set((article.translations or {}).keys())
        missing = [lang for lang in ARTICLE_TRANSLATION_LANGS if lang not in existing]
        if not missing:
            return 0
        celery_app.send_task(
            "app.tasks.newspaper.translate_article_batch", args=[str(article_id), missing]
        )
        return len(missing)
    except Exception:
        logger.warning("Failed to enqueue translation tasks for %s", article_id, exc_info=True)
        return 0


def enqueue_article_translations(article_id: str) -> None:
    """Fan out translate_article tasks for an article that just became feed- visible. Publish-time only: most held drafts never pass review, so translating at held/recompose time burns one Mistral call per target lang per dead draft."""
    enqueue_missing_article_translations(article_id)


def _auto_merge_redirect(*, original_url: str, final_url: str, service_id: str) -> None:
    """A scrape that resolves to a DIFFERENT registrable domain than requested (a real HTTP redirect — e.g. algonode.io -> nodely.io after a rebrand) is definitionally the same website. Auto-fold the current service into whichever service already owns the resolved domain, rather than the two polling and composing independently forever (the nodely.io/algonode.io duplicate-article incident). Mirrors the admin domain-approval flow's "attach to the existing owner" behavior for brand-new domains, just triggered by a redirect discovered on an ALREADY-tracked service instead of a fresh frontier approval. Best-effort: never blocks the compose in progress. No-op when same domain, no existing owner, or already merged."""
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
    set once at ingest and is reliable, so check it first.
    """
    if row.payload.get("source_kind") == "bluesky":
        return ""
    if _source_kind_from_url(row.scrape_url) != "web":
        return ""
    from app.modules.crawler.domain_tracker import domain_from_url

    return domain_from_url(row.scrape_url)


def _gate_enforces_review(
    *, clf_decision: object, title: str, body: str, page_text: str, source_url: str
) -> bool:
    """Quality veto on the auto-publish path. True when a draft Classifier A would send STRAIGHT to the feed (``clf_decision is True``) should instead be diverted to human review because the deterministic gatekeeper fails.

    Honors ``GATEKEEPER_ENFORCE`` — default off, so this returns False (shadow
    mode, no behaviour change) until the quality head is trusted. Failure-tolerant:
    a None gate (disabled / error) never diverts.
    """
    from app.core import config

    if clf_decision is not True or not config.GATEKEEPER_ENFORCE:
        return False
    from app.modules.gatekeeper.live import gate_draft

    gate = gate_draft(
        source_text=page_text,
        article_text=f"{title}\n{body}",
        service_id=source_url,
        source_url=source_url,
    )
    return gate is not None and not gate.passed


def _content_quality_fails(relevance: float, kind: PublishKind | None = None) -> bool:
    """Pre-compose veto: judge the actual context about to be handed to Mistral (page_text relevance, same 0-1 score_page scorer classify_pending_domains uses) so a poor-quality source never even reaches the writer.

    CONTENT_UPDATE gets its own, stricter CONTENT_UPDATE_QUALITY_FLOOR: a
    service is vetted once as a domain at discovery (lenient, first-crawl
    signal) but never re-checked per diff, so relevance can drift low over
    time without ever failing FRONTIER_CONTENT_REJECT_SCORE. This is a
    backstop behind the CONTENT_UPDATE_RELEVANCE_FLOOR enqueue-time gate
    (evaluate_enqueue), in case relevance shifts between ingest-time scoring
    and compose time. Other kinds keep sharing FRONTIER_CONTENT_REJECT_SCORE
    with the domain classifier.
    """
    from app.core import config

    if kind == PublishKind.CONTENT_UPDATE:
        return relevance < config.CONTENT_UPDATE_QUALITY_FLOOR
    return relevance < config.FRONTIER_CONTENT_REJECT_SCORE


def _effective_alert_topic(topic: PublishTopic, composed: ArticleComposeResult) -> PublishTopic:
    """Reader-facing topic for tags and match keys. The keyword topic classifier still ROUTES rows (priority, mandatory review — a false positive there only costs a review slot), but a scam/incident label only keeps its reader-facing consequences — the alert tag and the scam-topic match-key carve-out — when the writer confirmed it via the confirm_alert_topic tool. 2026-07-18: the Foundation's own homepage rebrand shipped toward readers tagged 'scam-alert' because a quoted research paper asked about 'malicious servers' — second false scam labeling in a week. Kept pure/tiny."""
    if topic not in (PublishTopic.SCAM_ALERT, PublishTopic.NETWORK_INCIDENT):
        return topic
    confirmed = getattr(composed, "confirmed_alert", None)
    if confirmed in (PublishTopic.SCAM_ALERT.value, PublishTopic.NETWORK_INCIDENT.value):
        # The writer's kind wins even when it differs from the keyword route.
        return PublishTopic(confirmed)
    return PublishTopic.GENERIC


def _quality_floor_fails(heuristic_grade: dict | None) -> bool:
    """Second quality veto on the auto-publish path: the writer's own two-stage grade/revise pass (article_grader.grade_article_draft, same score review_draft reports) falls below WRITER_QUALITY_FLOOR. Honors WRITER_QUALITY_GATE_ENABLED (default on). A missing/errored grade never diverts (fails open, matching _gate_enforces_review's failure-tolerant design)."""
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


def _fresh_auto_approve_passes(
    *,
    title: str,
    body: str,
    page_text: str,
    source_url: str,
    heuristic_grade: dict | None = None,
    defunct_domains: tuple[str, ...] = (),
    unsourced_hold_reason: str = "",
    broken_link_hold_reason: str = "",
) -> tuple[bool, dict[str, str]]:
    """Strict autonomous-approve gate for content that would otherwise wait for a human review click (owner decision 2026-07-12): grade + headline + gatekeeper factuality AND completeness must ALL clear a bar at least as strict as recompose_published's — fresh content has zero prior human vetting at all, unlike recompose which only touches content a human already approved once, so there's no argument for a looser bar here. Unlike recompose (which deliberately drops completeness from its gate — see the comment at its call site), fresh candidates are exactly what completeness's domain_provenance check exists to triage, so gate_ok uses gate.passed (factuality AND completeness), not factuality alone. Fails CLOSED: any missing or errored signal blocks auto-approve, never allows it. Always returns metadata (even on failure) for the review-row audit trail.

    ``heuristic_grade`` must be the FUSED grade (article_grader.fuse_quality_into_grade
    output — same dict ArticleComposeResult.heuristic_grade carries, already
    computed once during compose by the writer's own grade/revise loop) so
    FRESH_AUTO_APPROVE_GRADE_FLOOR is judged on the same scale as everywhere
    else in the pipeline. Root-caused 2026-08-25: this gate used to call
    grade_article_draft() fresh, which returns the schema/structure+length
    grade ONLY — the LLM rubric's narrative-quality judgment (75% of the
    weight everywhere else) had zero say in whether a fresh article skipped
    human review. Passing the already-fused compose-time grade costs nothing
    extra (no new LLM call — it's a dict already sitting on ``composed``) and
    is more correct than re-grading, since it's graded with the exact
    is_special_edition flag the compose itself used.
    """
    import json as _json

    from app.core import config as worker_config

    meta: dict[str, str] = {}
    # A defunct-entity hit blocks auto-approve unconditionally: the grade,
    # headline and gatekeeper checks below can all pass on a draft that
    # recommends a dead entity (the MyAlgo draft graded fine), so this must be
    # its own hard fail, not something the AND-gate could clear.
    if defunct_domains:
        meta["auto_applied"] = "0"
        meta["defunct_domains"] = ",".join(defunct_domains[:5])
        return False, meta
    # Same reasoning for unsourced specifics: grade/headline/gatekeeper can all
    # pass on a draft asserting a fabricated "1,000 issuers" (they can't see the
    # research trace), so this is its own hard fail, not something the AND-gate
    # below could clear back to auto-approve.
    if unsourced_hold_reason:
        meta["auto_applied"] = "0"
        meta["unsourced_hold_reason"] = unsourced_hold_reason[:200]
        return False, meta
    # Same reasoning again: an unverified broken-link claim can sit inside an
    # otherwise well-graded draft (grade/headline/gatekeeper don't read the
    # trace for click_element attempts), so this is its own hard fail too.
    if broken_link_hold_reason:
        meta["auto_applied"] = "0"
        meta["broken_link_hold_reason"] = broken_link_hold_reason[:200]
        return False, meta
    grade_value: float | None = None
    if heuristic_grade:
        try:
            grade_value = float(heuristic_grade["grade"])
            meta["grade"] = str(heuristic_grade["grade"])
            meta["grade_detail"] = _json.dumps(
                {
                    "subscores": heuristic_grade.get("subscores"),
                    "issues": heuristic_grade.get("issues"),
                },
                separators=(",", ":"),
            )
        except Exception:
            logger.warning(
                "fresh auto-approve grading failed for %s", source_url, exc_info=True
            )
    else:
        logger.warning("fresh auto-approve missing compose-time grade for %s", source_url)

    gate_ok = False
    try:
        from app.modules.gatekeeper.live import gate_draft

        gate = gate_draft(
            source_text=page_text,
            article_text=f"{title}\n{body}",
            service_id=source_url,
            source_url=source_url,
        )
        if gate is not None:
            meta.update(gate.as_metadata())
            gate_ok = gate.passed
        else:
            gate_ok = True  # gatekeeper disabled entirely — no signal to fail on
    except Exception:
        logger.warning(
            "fresh auto-approve gatekeeper check failed for %s", source_url, exc_info=True
        )

    from app.modules.newspaper.article_grader import headline_violations

    passed = (
        worker_config.FRESH_AUTO_APPROVE_ENABLED
        and grade_value is not None
        and grade_value >= worker_config.FRESH_AUTO_APPROVE_GRADE_FLOOR
        and not headline_violations(title)
        and gate_ok
    )
    meta["auto_applied"] = "1" if passed else "0"
    return passed, meta


@dataclass(frozen=True)
class _ComposeVetoCtx:
    """Everything the pre-compose vetoes need beyond the row itself."""

    row: QueuedPublishRow
    publish_kind: PublishKind
    compose_domain: str
    enforce_domain_cap: bool
    signals: ContentSignals


def _domain_cap_veto(ctx: _ComposeVetoCtx) -> dict | None:
    """Per-website daily article cap (COMPOSE_MAX_PER_DOMAIN_PER_DAY). ``enforce_domain_cap=False`` is available for a caller that must never hold a row on this cap alone (no live caller currently sets it — the old breaking-tier fast path was the last one, removed 2026-08-25)."""
    if not (ctx.enforce_domain_cap and ctx.compose_domain):
        return None
    from app.modules.crawler.domain_tracker import domain_compose_cap_reached

    if domain_compose_cap_reached(ctx.compose_domain):
        return {"status": "domain_capped", "service_id": ctx.row.service_id}
    return None


def _novelty_duplicate_veto(ctx: _ComposeVetoCtx) -> dict | None:
    """Near-duplicate guard: a very similar headline published recently means this compose would be spent on a repeat. Runs HERE (composition) not at enqueue, because more articles may have published since this was queued. "duplicate" is a terminal outcome, so the drain dequeues the row."""
    from app.core import config as worker_config

    if not worker_config.NOVELTY_GATE_ENABLED:
        return None
    from app.modules.newspaper.article_grader import (
        recent_content_similarity,
        recent_same_service_similarity,
        recent_title_similarity,
    )

    page_title = str(ctx.row.payload.get("page_title", ""))
    page_text = str(ctx.row.payload.get("page_text", ""))
    sim, closest = recent_title_similarity(page_title)
    if sim >= worker_config.NOVELTY_MAX_SIMILARITY:
        return {
            "status": "duplicate",
            "reason": "too_similar_to_recent",
            "service_id": ctx.row.service_id,
            "closest_title": closest,
            "similarity": round(sim, 2),
        }
    # Cross-service body-content check: recent_title_similarity above only
    # compares headlines, so a genuine CROSS-service duplicate with a
    # differently-worded title (the "brand-level" pattern -- Tinyman blog vs
    # app, Pera Wallet variants, Lofty AI, Valar, all found live 2026-08-20)
    # slips straight past it. queue_drain_tasks.py's _novelty_collapsed
    # already runs this same check at drain time; running it again here
    # covers the window between a row entering the drain and this specific
    # compose actually starting, same reasoning as recent_title_similarity's
    # own re-check above.
    content_sim, content_closest = recent_content_similarity(page_title, page_text)
    if content_sim >= worker_config.NOVELTY_MAX_SIMILARITY:
        return {
            "status": "duplicate",
            "reason": "too_similar_to_recent_content",
            "service_id": ctx.row.service_id,
            "closest_title": content_closest,
            "similarity": round(content_sim, 2),
        }
    # Same-service re-coverage gets a stricter bar: the Alpha Arcade pair
    # ("Goes Live with Daily ... Price Prediction Markets" vs "expands to
    # daily ... price markets") scored 0.455 — under the global gate, yet
    # plainly the same story ten days later (2026-07-16). BUT this compares
    # page_title (the scraped PAGE's <title> tag) against past ARTICLE
    # headlines -- fine for Alpha Arcade, where the source page's own title
    # is descriptive prose that tracks its content. A service-watch
    # aggregate's page_title is the site's static <title> tag (found
    # 2026-08-02, NFDomains: "NFD | nf.domains", identical on every poll
    # regardless of what changed) -- comparing it against generated headlines
    # can never match, so this check silently contributes nothing for every
    # aggregate-sourced compose. Skipped there rather than left to falsely
    # reassure: composed_duplicates_latest_service_article (post-compose,
    # the draft's own real title/body) is the enforcement point for this
    # shape now.
    is_aggregate_page = (
        str(ctx.row.payload.get("page_text", "")).lstrip().startswith("# SERVICE WATCH:")
    )
    if not is_aggregate_page:
        svc_sim, svc_closest = recent_same_service_similarity(page_title, ctx.row.service_id)
        if svc_sim >= worker_config.NOVELTY_SAME_SERVICE_MAX_SIMILARITY:
            return {
                "status": "duplicate",
                "reason": "too_similar_to_own_recent_coverage",
                "service_id": ctx.row.service_id,
                "closest_title": svc_closest,
                "similarity": round(svc_sim, 2),
            }
    return None


def _post_compose_duplicate_veto(
    composed: ArticleComposeResult, service_id: str, publish_kind: PublishKind
) -> dict | None:
    """Same-facts guard: catches a genuinely reworded source page that still reports the same facts as this service's own last article -- the shape _novelty_duplicate_veto's page_title/page_text comparison misses (Steak Pool, 2026-08-02). Runs post-compose (needs the draft's own numbers), CONTENT_UPDATE only -- discovery/breaking coverage has no meaningful "own prior article" to repeat."""
    if publish_kind != PublishKind.CONTENT_UPDATE:
        return None
    from app.modules.newspaper.article_grader import composed_duplicates_latest_service_article

    is_dup, prior_id, overlap = composed_duplicates_latest_service_article(
        title=composed.title, summary=composed.summary, body=composed.body, service_id=service_id
    )
    if not is_dup:
        return None
    return {
        "status": "duplicate",
        "reason": "same_facts_as_own_recent_article",
        "service_id": service_id,
        "closest_article_id": prior_id,
        "numeric_overlap": round(overlap, 2),
    }


def _pending_review_veto(ctx: _ComposeVetoCtx) -> dict | None:
    """A pending review already covers this exact URL — skip BEFORE paying for a Mistral compose, not after. This used to be a post-compose check only (still kept below as a safety net for the race window during a multi- minute compose), which meant a highly dynamic page — bank.testnet. algorand.network's wallet-connect/session chrome makes it register as "changed" on nearly every poll — could burn a full ~4min compose 5x in one day only to have every result but the first silently discarded with no article stored and no review ever updated (2026-07-10)."""
    from app.modules.crawler.classifier_review_store import has_pending_review_for_url

    if has_pending_review_for_url(ctx.row.scrape_url):
        return {"status": "duplicate_review_pending", "service_id": ctx.row.service_id}
    return None


def _content_quality_veto(ctx: _ComposeVetoCtx) -> dict | None:
    """Content-quality veto: judge the context BEFORE spending a Mistral call.

    queue_status "expired" retires the row: this snapshot's relevance can't
    improve until the next crawl, and the one-pending-per-service dedupe means
    a squatting sub-floor row would block that crawl's fresh signal from ever
    enqueueing (prod 2026-07-08: 10 rows from 07-03 recycling through every
    drain, so 'Pull Top topic' composed nothing).
    """
    if _content_quality_fails(ctx.signals.relevance, ctx.publish_kind):
        return {
            "status": "skipped",
            "reason": "poor_quality_content",
            "queue_status": "expired",
            "service_id": ctx.row.service_id,
            "relevance": round(ctx.signals.relevance, 3),
        }
    return None


# Evaluated in order, first non-None outcome wins. Same shape as the standard
# drain's _PRE_COMPOSE_GATES (queue_drain_tasks.py), but these vetoes return
# their full outcome dict — each carries different extra fields (similarity,
# relevance, ...) that the drain reports per row.
_PRE_COMPOSE_VETOES = (
    _pending_review_veto,
    _domain_cap_veto,
    _novelty_duplicate_veto,
    _content_quality_veto,
)


def _run_pre_compose_vetoes(ctx: _ComposeVetoCtx) -> dict | None:
    """First veto outcome for this compose, or None when all pass."""
    for veto in _PRE_COMPOSE_VETOES:
        outcome = veto(ctx)
        if outcome is not None:
            return outcome
    return None


def _try_composing_as_edit(row: QueuedPublishRow, payload: dict) -> dict[str, str] | None:
    """If this row is a still-open-window edit, apply it and return the result. Otherwise (never routed to edit, or the edit window has since closed) mutates payload to the create path and returns None so the caller proceeds with a fresh compose."""
    publish_mode = str(payload.get("publish_mode", "create"))
    linked_article_id = str(payload.get("linked_article_id", "")).strip()
    if not (publish_mode == "edit" and linked_article_id):
        return None

    from app.modules.newspaper.article_matching import is_edit_window_open

    if is_edit_window_open(linked_article_id):
        from app.modules.newspaper.article_edit_service import run_article_edit

        try:
            return run_article_edit(row)
        except ComposeBusyError:
            return {"status": "already_running", "key": COMPOSE_LOCK_KEY}
    # publish_mode was decided at INGEST time; a row can sit pending for
    # days behind cooldowns (observed: a 4-day-old edit row, 2026-07-17
    # audit) and drain long after the linked article's edit window closed.
    # Editing a days-old article from a stale routing decision is wrong —
    # fall through to the create path instead, which is exactly what
    # resolve_publish_mode would have decided today. Mutating the payload
    # keeps the downstream match-key registration (keyed on publish_mode
    # == "create") consistent with the path actually taken.
    logger.info(
        "edit window closed for linked article %s — composing as new article", linked_article_id
    )
    payload["publish_mode"] = "create"
    payload.pop("linked_article_id", None)
    return None


def _resolve_classifier_signals(
    row: QueuedPublishRow, payload: dict, page_text_for_clf: str
) -> tuple[ContentSignals, str, bool | None, float]:
    """Classifier verdict for the actual context about to be handed to Mistral.

    Computed once at ingest and carried on the payload; recomputed here for
    rows queued before signals existed. A null decision frozen into the
    payload at INGEST time would otherwise hold the article for review
    forever — even after the config that caused it changed (rows enqueued
    under training mode's sampling=1.0 all carry null) and even after the
    nightly retrain improves the model — so a frozen None is re-asked here
    with today's model and thresholds; only a fresh None (a genuinely
    low-confidence call) still routes to human review.
    """
    from app.modules.ai.content_signals import ContentSignals, compute_content_signals

    signals = ContentSignals.from_payload(payload.get("signals")) or compute_content_signals(
        page_text_for_clf, row.scrape_url
    )
    clf_category = signals.category
    clf_decision, clf_confidence = signals.publish_decision, signals.confidence
    if clf_decision is None:
        try:
            from app.modules.ai.publish_classifier import predict_publish

            clf_decision, clf_confidence = predict_publish(
                page_text_for_clf, row.scrape_url, clf_category
            )
        except Exception:
            logger.warning(
                "compose-time classifier refresh failed for %s", row.scrape_url, exc_info=True
            )
    return signals, clf_category, clf_decision, clf_confidence


def _suppress_if_dead_project(source_url: str, spike: object) -> None:
    """When the writer's abort_article call is category=dead_project, suppress the domain for a bounded cooldown instead of leaving it to re-fetch and re-abort at full research cost every cycle.

    Root-caused 2026-08-04 (Kryptonurd): abort_article(dead_project) was a
    correct, well-grounded call, but nothing downstream of it changed
    domain_tracking -- the domain stayed approved, so the next scheduled
    crawl re-composed and re-aborted, forever. Fail-open: a suppression
    failure must never turn a clean abort resolution into an error.
    """
    category = getattr(spike, "category", "")
    if category != "dead_project":
        return
    try:
        from app.core.config import DEAD_PROJECT_COOLDOWN_DAYS
        from app.modules.crawler.domain_tracker import (
            domain_from_url,
            suppress_dead_project_domain,
        )

        domain = domain_from_url(source_url or "")
        if not domain:
            return
        suppress_dead_project_domain(
            domain, days=DEAD_PROJECT_COOLDOWN_DAYS, reason=getattr(spike, "reason", "")
        )
    except Exception:
        logger.warning("dead-project domain suppression failed for %s", source_url, exc_info=True)


def _is_first_coverage(row: QueuedPublishRow, publish_kind: PublishKind) -> bool:
    """A CONTENT_UPDATE for a service with no published article would report "what changed" on a service readers have never met (its one-shot discovery row may have expired unpublished) — compose an introduction instead. Checked at compose time, not enqueue, so it adds zero enqueue dynamics (the old prior==0 discovery re-fire caused a queue flood)."""
    if publish_kind != PublishKind.CONTENT_UPDATE:
        return False
    from app.modules.newspaper.article_matching import service_has_article

    return not service_has_article(row.service_id)


def _compose_or_error(
    row: QueuedPublishRow,
    payload: dict,
    *,
    topic: PublishTopic,
    publish_kind: PublishKind,
    mistral_only: bool,
    enrichment_block: str,
    first_coverage: bool,
) -> tuple[ArticleComposeResult | None, dict[str, str] | None]:
    """Compose the article via the writer LLM. Returns (composed_result, None) on success, or (None, error_response) on a busy-lock, writer-spike, or LLM failure."""
    from app.modules.ai.llm_provider import LLMCreditError, LLMError
    from app.modules.ai.llm_purpose_router import PeakHoursBlockedError
    from app.modules.ai.story_spike import StorySpikedError

    prior_coverage_block = ""
    if not first_coverage:
        from app.modules.newspaper.article_grader import prior_service_article_summary

        prior_coverage_block = prior_service_article_summary(row.service_id)

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
            first_coverage=first_coverage,
            prior_coverage_block=prior_coverage_block,
            is_special_edition=bool(payload.get("is_special_edition", False)),
        )
        return composed, None
    except ComposeBusyError:
        return None, {"status": "already_running", "key": COMPOSE_LOCK_KEY}
    except StorySpikedError as spike:
        # The writer refused to compose this story (abort_article tool) — a
        # judgment call, resolved cleanly (no retry this cycle). An admin can
        # still recompose manually to override.
        logger.info(
            "writer spiked %s (%s): %s [%s]",
            row.service_id,
            row.scrape_url,
            spike.category,
            spike.reason,
        )
        _suppress_if_dead_project(row.scrape_url, spike)
        return None, {
            "status": "aborted_by_writer",
            "service_id": row.service_id,
            "reason": f"{spike.category}: {spike.reason}",
        }
    except LLMError as exc:
        if isinstance(exc, PeakHoursBlockedError):
            logger.info("compose deferred for %s (%s): %s", row.service_id, row.scrape_url, exc)
            return None, {"status": "skipped_peak_hours", "detail": str(exc)[:200]}
        credit_issue = isinstance(exc, LLMCreditError)
        status = "mistral_credit_insufficient" if credit_issue else "mistral_failed"
        logger.error(
            "LLM compose failed for %s (%s): %s",
            row.service_id,
            row.scrape_url,
            exc,
            exc_info=True,
        )
        return None, {"status": status, "service_id": row.service_id, "detail": str(exc)}


def _determine_review_divert(
    composed: ArticleComposeResult,
    *,
    clf_decision: bool | None,
    page_text_for_clf: str,
    source_url: str,
) -> tuple[bool, tuple[str, ...], str]:
    """Whether this draft must be diverted to human review, and why. Returns (gate_enforced_review, defunct_domains, hold_reason).

    A defunct-entity hit (body links a domain the research proved
    unreachable) is a hard divert regardless of grade — the prose likely
    recommends something dead, which a human must judge (MyAlgo incident
    2026-07-19). Unsourced hard specifics (fabricated traction/funding
    counts or named partners not in the research) are a hard divert too — a
    human must judge whether the specific is real-but-unsourced or invented
    (GoPlausible incident 2026-07-20).
    """
    defunct_domains = tuple(getattr(composed, "defunct_domains", ()) or ())
    unsourced_hold_reason = str(getattr(composed, "unsourced_hold_reason", "") or "")
    broken_link_hold_reason = str(getattr(composed, "broken_link_hold_reason", "") or "")
    gate_enforced_review = (
        _gate_enforces_review(
            clf_decision=clf_decision,
            title=composed.title,
            body=composed.body,
            page_text=page_text_for_clf,
            source_url=source_url,
        )
        or _quality_floor_fails(getattr(composed, "heuristic_grade", None))
        or bool(defunct_domains)
        or bool(unsourced_hold_reason)
        or bool(broken_link_hold_reason)
    )
    if defunct_domains:
        logger.warning(
            "defunct-entity gate diverting %s to review — dead linked domain(s): %s",
            source_url,
            ", ".join(defunct_domains),
        )
    if unsourced_hold_reason:
        logger.warning(
            "unsourced-specifics gate diverting %s to review — %s",
            source_url,
            unsourced_hold_reason,
        )
    if broken_link_hold_reason:
        logger.warning(
            "broken-link-claim gate diverting %s to review — %s",
            source_url,
            broken_link_hold_reason,
        )
    # Human-readable divert reason for the review card, so a reviewer sees WHAT
    # tripped the hold (which dead domain / which unsourced specifics) instead of
    # a bare "diverted_by: gatekeeper" and having to re-read the whole draft.
    hold_reasons: list[str] = []
    if defunct_domains:
        hold_reasons.append("dead linked domain(s): " + ", ".join(defunct_domains[:5]))
    if unsourced_hold_reason:
        hold_reasons.append(unsourced_hold_reason)
    if broken_link_hold_reason:
        hold_reasons.append(broken_link_hold_reason)
    return gate_enforced_review, defunct_domains, "; ".join(hold_reasons)


def _resolve_hero_and_image(
    payload: dict, row: QueuedPublishRow, composed: ArticleComposeResult
) -> tuple[str, str]:
    """Resolve a hero/brand image when the upstream payload carried none, so both the feed tile and the social/OG card show real artwork (best-effort). A true share image (og/twitter) is also embedded in the body; a brand logo populates image_url only (it's not a body banner). Returns (hero_image, image_field)."""
    payload_og = _validated_hero_checked(str(payload.get("og_image", "")).strip(), row.scrape_url)
    if payload_og:
        return payload_og, payload_og
    try:
        from app.modules.newspaper.source_image import resolve_article_images

        # body-sources fallback covers lanes with no fetchable source_url
        # (editorial://brief/…, mail://message/…): the writer's own cited
        # research links are the only place a real image can come from.
        # Validation runs INSIDE the resolver (anchored to the page that
        # declared each image) so a dead declared og:image can't
        # short-circuit past the cited-links fallback.
        og, logo = resolve_article_images(
            source_url=row.scrape_url,
            service_id=row.service_id,
            body=composed.body,
            validate=_validated_hero_checked,
        )
        return og, (og or logo)
    except Exception:
        logger.warning("failed to resolve source images for %s", row.scrape_url, exc_info=True)
        return "", ""


def _pacing_open_for_auto_approve(tier: PublishTier) -> bool:  # noqa: ARG001 -- tier kept for API stability, see PublishTier's docstring
    from app.modules.newspaper.publish_policy import remaining_standard_publish_slots
    from app.modules.newspaper.publish_schedule import is_standard_publish_due

    due, _due_detail = is_standard_publish_due()
    return due and remaining_standard_publish_slots() > 0


def _maybe_auto_approve(
    *,
    needs_review: bool,
    tier: PublishTier,
    composed: ArticleComposeResult,
    page_text_for_clf: str,
    row: QueuedPublishRow,
    payload: dict,
    defunct_domains: tuple[str, ...],
    unsourced_hold_reason: str,
    broken_link_hold_reason: str,
    clf_category: str,
    clf_confidence: float,
    signals: ContentSignals,
    gate_enforced_review: bool,
    image_field: str,
) -> tuple[bool, bool]:
    """Autonomous mode for fresh content (owner decision 2026-07-12): content the classifier wasn't confident about no longer waits on a human click if it clears the same strict AND-gate recompose uses (grade + headline + gatekeeper factuality). A review row is still written and immediately resolved "auto_approved" so the audit trail stays visible in admin — deliberately never fed into classifier_feedback (that table trains from HUMAN labels; an auto-decision isn't one). Returns (needs_review, route_to_backlog)."""
    if not needs_review:
        return needs_review, False
    fresh_auto_approved, fresh_auto_meta = _fresh_auto_approve_passes(
        title=composed.title,
        body=composed.body,
        page_text=page_text_for_clf,
        source_url=row.scrape_url,
        heuristic_grade=composed.heuristic_grade,
        defunct_domains=defunct_domains,
        unsourced_hold_reason=unsourced_hold_reason,
        broken_link_hold_reason=broken_link_hold_reason,
    )
    if not fresh_auto_approved:
        return needs_review, False

    # An auto-approved article is approved, not exempt from cadence: it may
    # publish NOW only when the standard interval has elapsed and a daily
    # slot is free; otherwise the finished draft is stored unlisted and
    # queued in pending_feed_queue for the paced backlog release. Without
    # this gate a drain run with several auto-approvable rows chain-
    # published them minutes apart — three articles in a row on 2026-07-15,
    # because the review branch bypasses publish pacing by design and an
    # "auto-published" outcome advanced neither the pacing clock nor the run
    # budget.
    pacing_open = _pacing_open_for_auto_approve(tier)
    if pacing_open:
        needs_review = False
    route_to_backlog = not pacing_open

    from app.modules.crawler.classifier_review_store import (
        complete_classifier_review,
        enqueue_classifier_review,
    )

    auto_review_id = enqueue_classifier_review(
        url=row.scrape_url,
        page_text=page_text_for_clf,
        page_title=str(payload.get("page_title", "")) or composed.title,
        category=clf_category,
        storage_score=signals.storage_score,
        metadata={
            "source": _source_kind_from_url(row.scrape_url) or "web",
            "confidence": f"{clf_confidence:.3f}",
            "categories": ",".join(signals.categories),
            "diverted_by": "gatekeeper" if gate_enforced_review else "classifier",
            "og_image": image_field,
            "service_id": row.service_id,
            "auto_route": "backlog" if route_to_backlog else "publish",
            **fresh_auto_meta,
        },
    )
    complete_classifier_review(auto_review_id, resolution="auto_approved")
    return needs_review, route_to_backlog


def _grade_and_gate(
    composed: ArticleComposeResult,
    *,
    title: str,
    source_url: str,
    page_text: str,
    service_id: str,
    label: str = "",
) -> tuple[dict[str, str], float | None, bool]:
    """Quality grade + deterministic gatekeeper for one draft, in the shape every caller needs.

    Returns (grade_meta, grade_value, gate_ok). The held-for-review and
    recompose-review paths only want grade_meta — the metadata a human reviewer
    sees next to the draft — and ignore the rest; the archive-refresh path also
    needs the numbers to decide whether to auto-apply.

    Both stages fail soft and independently: a grader or gatekeeper error must
    never stop a draft being stored, it just means the reviewer sees less.

    gate_ok reflects factuality (numeric entailment) only, not the completeness
    rule's OSINT-tool-call check, and is True when the gatekeeper is disabled
    entirely (no signal to fail on). Completeness fires on any source mentioning
    a website/founder/company — nearly every service-profile source — while the
    writer only sporadically calls the matching tools, so gating on it blocked
    ~all Tier-2 recomposes regardless of quality (owner confirmed 2026-07-12:
    grades were consistently 7.3-10 while completeness failed almost
    universally). Still recorded in grade_meta for visibility.

    This exclusion is a property of THIS function, so it applies identically
    to every caller that reaches gate_draft() through it: _hold_for_review
    (discards gate_ok — a held draft is held regardless), recompose_review
    (same, discards gate_ok — always routes to a human), and
    recompose_published's auto_apply decision (DOES gate on gate_ok). There
    is no separate, stricter completeness-inclusive path for recompose —
    a 2026-08-25 audit of classifier_review_queue outcomes (33 resolved
    holds, 76% human-override rate) suspected recompose_published still
    gated on gate.passed (factuality AND completeness), by analogy with
    _fresh_auto_approve_passes which legitimately does use gate.passed for
    FRESH content (see that function's docstring — zero prior human vetting
    there, unlike recompose). That suspicion does not hold: git history
    shows recompose_published had its own dedicated completeness-exclusion
    (originally _recompose_published_grade_and_gate, same "owner confirmed
    2026-07-12" reasoning) from the same day as this function's, well before
    the 2026-07-26 refactor (5011077) collapsed all three near-duplicate
    grade/gate helpers into this one — so recompose_published has never used
    gate.passed here. The gk_completeness:"fail" reason string showing up on
    both later-approved and later-rejected review rows in that audit isn't a
    gating bug; it's expected, since completeness is deliberately
    display-only metadata on this path (per the paragraph above) and was
    never wired to block or allow anything for recompose — a human reviewer
    may or may not weigh it, but the code doesn't.

    The grade is read straight off ``composed.heuristic_grade`` — the FUSED
    grade (article_grader.fuse_quality_into_grade: schema/structure+length
    AND the LLM quality rubric) the writer's own grade/revise loop already
    computed once during compose — rather than re-running
    grade_article_draft() here. Two reasons: (1) it's free (no new LLM call,
    just reading a dict already on ``composed``), and (2) root-caused
    2026-08-25: this function used to call grade_article_draft() fresh,
    which is schema-only and gives the LLM rubric's narrative-quality
    judgment zero weight — exactly the gap fuse_quality_into_grade's
    2026-08-06 fix was meant to close everywhere, but this shared
    held-for-review/recompose helper (and RECOMPOSE_AUTO_APPLY_GRADE_FLOOR's
    gate at its recompose_published call site) kept computing the old
    schema-only number instead. This also makes the is_special_edition
    threading this function used to need for its own grade_article_draft
    call moot: composed.heuristic_grade was already graded during compose
    with the correct is_special_edition flag (llm_compose._grade_current_draft
    receives it from the same compose call that produced ``composed``).
    """
    import json as _json

    from app.core import config as worker_config

    grade_meta: dict[str, str] = {}
    grade_value: float | None = None
    heuristic_grade = composed.heuristic_grade
    if heuristic_grade:
        try:
            grade_value = float(heuristic_grade["grade"])
            grade_meta = {
                "grade": str(heuristic_grade["grade"]),
                "grade_detail": _json.dumps(
                    {
                        "subscores": heuristic_grade.get("subscores"),
                        "issues": heuristic_grade.get("issues"),
                    },
                    separators=(",", ":"),
                ),
            }
        except Exception:
            grade_meta = {}

    gate_ok = False
    try:
        from app.modules.gatekeeper.live import gate_draft

        gate = gate_draft(
            source_text=page_text,
            article_text=f"{title}\n{composed.body}",
            service_id=service_id,
            source_url=source_url,
        )
        if gate is not None:
            grade_meta.update(gate.as_metadata())
            # Completeness is deliberately excluded here (see this function's own
            # docstring: it over-fired on ~every Tier-2 source, owner confirmed
            # 2026-07-12) -- but a dead-domain reference is a distinct, always-on
            # safety check, not a completeness rule, so it hard-fails regardless.
            gate_ok = (
                gate.factuality_score >= worker_config.GATEKEEPER_FACT_MIN
                and not gate.dead_domains
            )
        else:
            gate_ok = True  # gatekeeper disabled entirely — no signal to fail on
    except Exception:
        logger.warning("gatekeeper grading failed for %s", label or service_id, exc_info=True)
    return grade_meta, grade_value, gate_ok


def _hold_for_review(
    row: QueuedPublishRow,
    payload: dict,
    composed: ArticleComposeResult,
    *,
    tier: PublishTier,
    topic: PublishTopic,
    publish_kind: PublishKind,
    compose_domain: str,
    clf_category: str,
    clf_confidence: float,
    signals: ContentSignals,
    gate_enforced_review: bool,
    hold_reason: str,
    hero_image: str,
    image_field: str,
    route_to_backlog: bool,
    page_text_for_clf: str,
) -> dict[str, str]:
    """Store the composed draft unpublished and route it to the review queue (or the approved-but-capped backlog). Terminal — always returns a result dict."""
    from app.modules.crawler.classifier_review_store import (
        enqueue_classifier_review,
        has_pending_review_for_url,
    )
    from app.modules.newspaper.article_store import insert_stored_article
    from app.modules.newspaper.security import sanitize_body

    if has_pending_review_for_url(row.scrape_url):
        return {"status": "duplicate_review_pending", "service_id": row.service_id}
    from app.modules.crawler.classifier_review_store import review_queue_full

    # review_queue_full() is ALSO checked upstream, before compose starts
    # (queue_drain_tasks.py), specifically to avoid burning a Mistral
    # compose on a row whose review can't land — that is the real
    # protection. By the time we're here the compose (minutes) has already
    # run, so this is only a race window: the queue can fill mid-compose.
    # Root-caused live 2026-08-10 (Pixel City / pixelcity-aetheralabs-es):
    # this branch used to discard the finished draft outright, exactly the
    # "throw away a finished compose" failure already fixed once for the
    # daily-cap race (_stash_capped_compose_to_backlog). enqueue_classifier_review
    # has no hard capacity limit — MAX_PENDING_REVIEWS is an advisory
    # compose-time throttle, not a storage invariant — so store and enqueue
    # anyway; the reviewer just sees two pending items instead of one.
    if not route_to_backlog and review_queue_full():
        logger.warning(
            "review queue filled mid-compose for %s — storing and enqueuing "
            "anyway instead of discarding the finished draft",
            row.service_id,
        )

    held_kind = _source_kind_from_url(row.scrape_url)
    held_title, held_summary = composed.title, composed.summary
    held_tags = _merge_tags(
        derive_article_tags(
            service_id=row.service_id,
            source_kind=held_kind,
            title=held_title,
            publish_kind=composed.publish_kind or publish_kind.value,
            publish_topic=_effective_alert_topic(topic, composed).value,
            publish_tier=tier.value,
        ),
        getattr(composed, "extra_tags", ()),
    )
    from datetime import UTC as _held_UTC
    from datetime import datetime as _held_datetime

    _held_approved_at = _held_datetime.now(tz=_held_UTC) if route_to_backlog else None
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
        status="backlog" if route_to_backlog else "on_hold",
        image_url=image_field,
        tags=held_tags,
        prompt_version=getattr(composed, "prompt_version", ""),
        interest_score=0.0 if route_to_backlog else None,
        approved_at=_held_approved_at,
    )
    # Grade the draft so the human reviewer sees a quality score + reasons.
    grade_meta, _grade_value, _gate_ok = _grade_and_gate(
        composed,
        title=held_title,
        source_url=row.scrape_url,
        page_text=page_text_for_clf,
        service_id=row.scrape_url,
    )
    review_id = ""
    if not route_to_backlog:
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
                # recompose_review carries these forward — without og_image
                # here, every recomposed article silently lost its image.
                "og_image": image_field,
                "service_id": row.service_id,
                **({"hold_reason": hold_reason[:400]} if hold_reason else {}),
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
            mark_brief_run(brief_id=str(payload.get("brief_id", "")), article_id=held_article_id)
    if route_to_backlog:
        # Approved but the cadence/cap is closed: the article was already
        # stored with status='backlog' (and interest_score/approved_at set)
        # above -- _release_pending_feed_backlog ships it later at the
        # standard pace (re-stamping published_at at release, same as the
        # admin approve-when-capped path).
        return {
            "status": "approved_backlog",
            "service_id": row.service_id,
            "article_id": held_article_id,
        }
    return {
        "status": "review",
        "service_id": row.service_id,
        "article_id": held_article_id,
        "review_id": review_id,
    }


def _reserve_slot_or_backlog(
    row: QueuedPublishRow,
    composed: ArticleComposeResult,
    payload: dict,
    *,
    hero_image: str,
    image_field: str,
    publish_kind: PublishKind,
    topic: PublishTopic,
    tier: PublishTier,
) -> dict[str, str] | None:
    """Reserve a publish slot for this tier, or hand a STANDARD-tier draft to the capped-backlog stash when the cap/cadence is closed. Returns None when a slot was reserved (proceed to publish), else the terminal result dict."""
    from app.modules.newspaper.publish_daily_guard import (
        PublishCapExceededError,
        assert_publish_allowed,
        reserve_publish_slot,
    )

    try:
        assert_publish_allowed(tier=tier)
        reserved, reserve_reason = reserve_publish_slot(tier=tier)
        if not reserved:
            if tier == PublishTier.STANDARD:
                return _stash_capped_compose_to_backlog(
                    row=row,
                    composed=composed,
                    payload=payload,
                    hero_image=hero_image,
                    image_field=image_field,
                    publish_kind=publish_kind,
                    topic=topic,
                    tier=tier,
                    reason=reserve_reason,
                )
            return {"status": "rate_limited", "reason": reserve_reason, "tier": tier.value}
    except PublishCapExceededError as exc:
        if tier == PublishTier.STANDARD:
            return _stash_capped_compose_to_backlog(
                row=row,
                composed=composed,
                payload=payload,
                hero_image=hero_image,
                image_field=image_field,
                publish_kind=publish_kind,
                topic=topic,
                tier=tier,
                reason=str(exc),
            )
        return {"status": "rate_limited", "reason": str(exc), "tier": tier.value}
    return None


def _finalize_publish(
    row: QueuedPublishRow,
    payload: dict,
    composed: ArticleComposeResult,
    *,
    hero_image: str,
    image_field: str,
    publish_kind: PublishKind,
    topic: PublishTopic,
    tier: PublishTier,
    compose_domain: str,
) -> dict[str, str]:
    """Publish the composed draft straight to the live feed: insert, index, distribute, ping IndexNow, register match keys, record compose cadence, and enqueue translations."""
    from app.modules.newspaper.publish_daily_guard import release_publish_slot
    from app.modules.newspaper.security import sanitize_body

    title, summary, body = composed.title, composed.summary, sanitize_body(composed.body)
    body = _with_hero_image(body, hero_image, title, source_url=row.scrape_url)
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
                    publish_topic=_effective_alert_topic(topic, composed).value,
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
    # Auto-post to social channels (Bluesky, Telegram, ...) — best-effort,
    # each channel isolated from the others, never blocks the publish itself.
    try:
        from app.modules.newspaper.tasks.distribution_tasks import distribute_article

        distribute_article.delay(article_id=article_id)
    except Exception:
        logger.warning("failed to queue distribution for article %s", article_id, exc_info=True)
    # Notify IndexNow (Bing/Ecosia/DuckDuckGo, Yandex, Seznam, Naver) so the new
    # story gets crawled in minutes. Best-effort — never let it block a publish.
    try:
        from app.modules.newspaper.article_store import ensure_article_slug
        from app.modules.newspaper.indexnow import ping_article

        ping_article(article_id, slug=ensure_article_slug(article_id, title))
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


def _classify_publish_outcome(result: dict) -> str | None:
    """The compose_sessions terminal status a publish_from_queued_row outcome should be finalized to, or None to leave any "ok" row alone.

    Root-caused 2026-08-04 (GoPlausible): "ok" only means the compose
    produced a JSON payload without crashing -- it says nothing about
    whether that draft got published, held, or rejected, so the two looked
    identical in the admin Sessions view. Deliberately narrow: pre-compose
    vetoes, compose failures, and edit-window fallbacks either never reached
    "ok" at all (nothing to overwrite) or already carry a self-explanatory
    status of their own (aborted_by_writer, error, credit_insufficient) --
    only "a draft WAS produced, then a decision was made about it" cases
    need translating. Covers both the create path's vocabulary (published/
    review/duplicate) and the article-edit path's (edited/failed) -- caught
    live 2026-08-04 when the Humanitarian Network special edition's own
    recompose landed as "edited" and this function didn't recognize it,
    leaving its session stuck at the same ambiguous "ok" this exists to fix.
    """
    status = str(result.get("status", ""))
    reason = str(result.get("reason", "") or result.get("hold_reason", ""))
    if status in ("published", "approved_backlog", "auto_applied", "edited"):
        return "published"
    if status == "review":
        return "on_hold"
    if status == "duplicate":
        return f"rejected:{reason}" if reason else "rejected"
    if status == "failed":
        return f"rejected:{reason}" if reason else "rejected:failed"
    return None


def publish_from_queued_row(
    row: QueuedPublishRow,
    *,
    publish_tier: PublishTier | None = None,
    enforce_domain_cap: bool = True,
) -> dict[str, str]:
    """Compose and insert one queue item (caller marks queue done); thin wrapper around _publish_from_queued_row_impl that also finalizes the compose session's status='ok' placeholder into the real publish decision (see _classify_publish_outcome)."""
    result = _publish_from_queued_row_impl(
        row, publish_tier=publish_tier, enforce_domain_cap=enforce_domain_cap
    )
    outcome = _classify_publish_outcome(result)
    if outcome:
        with contextlib.suppress(Exception):
            from app.modules.ai.tool_insights_store import finalize_compose_session_outcome

            finalize_compose_session_outcome(row.scrape_url, outcome)
    return result


def _stamp_service_recompose_cooldown(service_id: str, *, ok: bool) -> None:
    """Stamp the SAME re-scrape cooldown run_llm_diff_check's own loop checks (scrape_throttled/mark_scraped, SERVICE_RESCRAPE_DAYS) -- for ANY compose attempt on this service, not just ones the beat itself triggered.

    Without this, an admin "Recompose now" (which deliberately bypasses the
    pacing CHECK, by design) never stamped the cooldown either, so the beat
    could turn around and recompose the same service again within days
    (found live 2026-08-09: algoseas.io recomposed 2 days after a manual
    compose, well inside the 30-day window). A failed compose still stamps
    -- just the short failure backoff, not the full window -- so a
    transient error doesn't block a legitimate retry for 30 days.
    """
    with contextlib.suppress(Exception):
        from app.modules.scraper.core.scrape_cooldown import mark_scraped

        mark_scraped(service_id, ok=ok)


@single_flight(lambda row, **_kw: f"compose:{row.queue_id}", ttl=1800)
def _publish_from_queued_row_impl(
    row: QueuedPublishRow,
    *,
    publish_tier: PublishTier | None = None,
    enforce_domain_cap: bool = True,
) -> dict[str, str]:
    """Compose and insert one queue item (caller marks queue done).

    Two stacked single_flight locks:
    - ``compose:{queue_id}`` (per-row): a row is never composed twice at once.

    The global ``compose:article`` mutex is acquired inside the Mistral writer
    entry points (``_compose_via_writer_tools``, edit compose) so every path —
    queue drain, admin recompose, editorial assignment — shares one gate.

    A concurrent caller gets ``already_running`` and leaves its row pending for
    the next beat.
    """
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

    edit_result = _try_composing_as_edit(row, payload)
    if edit_result is not None:
        return edit_result

    # We ALWAYS resolve the compose domain (registrable: domain_from_url
    # collapses forum.folks.finance -> folks.finance, so subdomains of one
    # project share the cap/cooldown) so the compose is RECORDED below —
    # stamping the cooldown that spaces out the next article — even when
    # enforce_domain_cap is False and the cap veto itself is skipped.
    compose_domain = _compose_domain_for_row(row)

    page_text_for_clf = str(payload.get("page_text", ""))
    signals, clf_category, clf_decision, clf_confidence = _resolve_classifier_signals(
        row, payload, page_text_for_clf
    )

    # Pre-compose vetoes (_PRE_COMPOSE_VETOES): pending review already covers
    # this URL, per-website daily cap, near-duplicate headline, content
    # quality — in that order, before any enrichment gathering or Mistral spend.
    veto_outcome = _run_pre_compose_vetoes(
        _ComposeVetoCtx(
            row=row,
            publish_kind=publish_kind,
            compose_domain=compose_domain,
            enforce_domain_cap=enforce_domain_cap,
            signals=signals,
        )
    )
    if veto_outcome is not None:
        return veto_outcome

    enrichment_block = enrichment_block_for_row(
        row, payload, topic, is_first_snapshot=bool(payload.get("is_first_snapshot", False))
    )
    first_coverage = _is_first_coverage(row, publish_kind)

    composed, compose_error = _compose_or_error(
        row,
        payload,
        topic=topic,
        publish_kind=publish_kind,
        mistral_only=mistral_only,
        enrichment_block=enrichment_block,
        first_coverage=first_coverage,
    )
    _stamp_service_recompose_cooldown(row.service_id, ok=compose_error is None)
    if compose_error is not None:
        return compose_error

    duplicate_outcome = _post_compose_duplicate_veto(composed, row.service_id, publish_kind)
    if duplicate_outcome is not None:
        return duplicate_outcome

    # Classifier gate: only confidently publish-worthy content goes straight
    # to the feed. Everything else is stored unpublished and queued for admin
    # review — approving the review item publishes the article.

    # Quality veto on the auto-publish path: a draft Classifier A would send
    # straight to the feed is diverted into the human-review path below when the
    # deterministic gatekeeper fails under GATEKEEPER_ENFORCE (default off).
    gate_enforced_review, defunct_domains, hold_reason = _determine_review_divert(
        composed,
        clf_decision=clf_decision,
        page_text_for_clf=page_text_for_clf,
        source_url=row.scrape_url,
    )
    unsourced_hold_reason = str(getattr(composed, "unsourced_hold_reason", "") or "")
    broken_link_hold_reason = str(getattr(composed, "broken_link_hold_reason", "") or "")

    hero_image, image_field = _resolve_hero_and_image(payload, row, composed)

    needs_review = clf_decision is not True or gate_enforced_review
    needs_review, route_to_backlog = _maybe_auto_approve(
        needs_review=needs_review,
        tier=tier,
        composed=composed,
        page_text_for_clf=page_text_for_clf,
        row=row,
        payload=payload,
        defunct_domains=defunct_domains,
        unsourced_hold_reason=unsourced_hold_reason,
        broken_link_hold_reason=broken_link_hold_reason,
        clf_category=clf_category,
        clf_confidence=clf_confidence,
        signals=signals,
        gate_enforced_review=gate_enforced_review,
        image_field=image_field,
    )

    if needs_review or route_to_backlog:
        return _hold_for_review(
            row,
            payload,
            composed,
            tier=tier,
            topic=topic,
            publish_kind=publish_kind,
            compose_domain=compose_domain,
            clf_category=clf_category,
            clf_confidence=clf_confidence,
            signals=signals,
            gate_enforced_review=gate_enforced_review,
            hold_reason=hold_reason,
            hero_image=hero_image,
            image_field=image_field,
            route_to_backlog=route_to_backlog,
            page_text_for_clf=page_text_for_clf,
        )

    slot_result = _reserve_slot_or_backlog(
        row,
        composed,
        payload,
        hero_image=hero_image,
        image_field=image_field,
        publish_kind=publish_kind,
        topic=topic,
        tier=tier,
    )
    if slot_result is not None:
        return slot_result

    return _finalize_publish(
        row,
        payload,
        composed,
        hero_image=hero_image,
        image_field=image_field,
        publish_kind=publish_kind,
        topic=topic,
        tier=tier,
        compose_domain=compose_domain,
    )


@celery_app.task(
    name="app.tasks.newspaper.compose_queue_row_now",
    soft_time_limit=worker_config.COMPOSE_TASK_SOFT_TIME_LIMIT,
    time_limit=worker_config.COMPOSE_TASK_TIME_LIMIT,
)
def compose_queue_row_now(queue_id: str) -> dict[str, str]:
    """Admin-triggered immediate compose of one publish_queue row, bypassing is_standard_publish_due — the pacing gate that normally decides whether drain_standard_publish_queue is even allowed to run at all, checked inside the drain task itself rather than inside publish_from_queued_row. This is a deliberate manual override ("compose this NOW, I want to see the pipeline behave") added 2026-08-05 after repeatedly needing a one-off script to bypass the gate by hand during pipeline testing. The automatic drain's own pacing is untouched — only this admin-triggered path skips it.

    Same "still pending" guard shape as recompose_review, for the same
    reason: a stale/duplicate trigger (an unrefreshed admin tab, a double
    click, an old queue_id from before an earlier resolve) must not re-run
    compose on top of a row that already resolved.
    """
    from uuid import UUID

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import PublishQueueStmts
    from app.modules.newspaper.publish_queue_store import get_queued_row

    try:
        qid = UUID(str(queue_id))
    except ValueError:
        return {"status": "error", "reason": "bad_queue_id"}

    status_row = get_cassandra_session().execute(PublishQueueStmts.GET_STATUS_ROW, (qid,)).one()
    if status_row is None:
        return {"status": "error", "reason": "queue_row_not_found"}
    if (status_row.status or "").strip().lower() != "pending":
        return {
            "status": "error",
            "reason": f"row not pending (status={status_row.status!r}) — refused",
        }

    row = get_queued_row(queue_id)
    if row is None:
        return {"status": "error", "reason": "queue_row_not_found"}

    outcome = publish_from_queued_row(row)

    from app.modules.newspaper.tasks.queue_drain_tasks import _resolve

    _resolve(row, outcome)
    return outcome


def _publish_pipeline_precheck(
    service_id: str, scrape_url: str, txid: str
) -> dict[str, str] | None:
    """Early skip gates evaluated before the (paid) scrape: publish-cap saturation, per-source cooldown, a crawl-disabled domain, and a domain the content-relevance classifier has flagged poor-quality. Returns a skip result, or None to proceed."""
    from app.core import config as worker_config
    from app.modules.newspaper.publish_daily_guard import is_standard_publish_saturated
    from app.modules.scraper.core.scrape_cooldown import is_on_cooldown

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
    if scrape_url.lower().startswith(("http://", "https://", "browser://")):
        from app.modules.crawler.domain_tracker import domain_from_url, is_dead_end_domain

        early_domain = domain_from_url(scrape_url)
        if early_domain and is_dead_end_domain(early_domain):
            return {"status": "skipped", "reason": "poor_quality_source", "txid": txid}
    return None


def _domain_compose_cap_skip(
    source_kind: str | None, scrape_url: str, txid: str
) -> dict[str, str] | None:
    """Per-domain daily article cap (web sources): a churning page must not be re-composed every poll. Early-out here to avoid enqueuing a candidate the compose stage would reject anyway — the count itself is incremented when an article is actually created (see publish_from_queued_row), so this read is purely an optimization."""
    if source_kind != "web":
        return None
    from app.modules.crawler.domain_tracker import domain_compose_cap_reached, domain_from_url

    compose_domain = domain_from_url(scrape_url)
    if compose_domain and domain_compose_cap_reached(compose_domain):
        return {"status": "skipped", "reason": "domain_daily_compose_cap", "txid": txid}
    return None


def _enqueue_web_page_links(source_kind: str | None, result: object, scrape_url: str) -> None:
    if source_kind != "web":
        return
    try:
        from app.modules.scraper.core.link_extractor import enqueue_page_links

        enqueue_page_links(raw_html=result.raw_html, page_url=scrape_url, source="web")
    except Exception:
        logger.warning("failed to enqueue page links from %s", scrape_url, exc_info=True)


def _publish_pipeline_page_text(
    *, service_id: str, display_name: str, scrape_url: str, result: object, source_kind: str | None
) -> str:
    """Aggregated service-context text for web sources when enabled, else the raw scraped page text.

    For web services the snapshot/diff/compose unit is the service's
    aggregated recent pages (all its domains), never just this one URL.
    Falls back to the single page when there is no harvest yet or
    aggregation fails. Also re-queues the aggregate's pages for a fresh
    crawl so next week's aggregate reflects current content.
    """
    from app.core import config as worker_config

    if source_kind != "web" or not worker_config.SERVICE_CONTEXT_ENABLED:
        return result.text
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
        return page_text
    except Exception:
        logger.warning(
            "service context aggregation failed for %s — using entry page only",
            service_id,
            exc_info=True,
        )
        return result.text


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
    from app.modules.scraper.core.scrape_cooldown import (
        clear_scrape_cooldown,
        record_scrape_failure,
    )

    precheck_skip = _publish_pipeline_precheck(service_id, scrape_url, txid)
    if precheck_skip is not None:
        return precheck_skip

    lower_scrape_url = scrape_url.lower()
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
    cap_skip = _domain_compose_cap_skip(source_kind, scrape_url, txid)
    if cap_skip is not None:
        return cap_skip
    _enqueue_web_page_links(source_kind, result, scrape_url)

    page_text = _publish_pipeline_page_text(
        service_id=service_id,
        display_name=display_name,
        scrape_url=scrape_url,
        result=result,
        source_kind=source_kind,
    )

    return ingest_publish_signal(
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


@celery_app.task(
    name="app.tasks.newspaper.publish_from_chain_event",
    soft_time_limit=worker_config.COMPOSE_TASK_SOFT_TIME_LIMIT,
    time_limit=worker_config.COMPOSE_TASK_TIME_LIMIT,
)
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
    """Celery task: run the publish pipeline for a chain-detected match, skipping if unresolvable."""
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


def _recompose_via_writer(
    *,
    review_id: str,
    url: str,
    page_text: str,
    page_title: str,
    category: str,
    storage_score: float,
    kind: str | None,
    old_article_id: str,
) -> tuple[ArticleComposeResult | None, dict[str, str] | None]:
    """Compose a fresh proposal for a recompose. Returns (composed, None) on success, or (None, error_response) on a busy-lock, writer-spike, or LLM failure — restoring/re-enqueuing the review on failure so it isn't lost."""
    from app.modules.ai.llm_provider import LLMCreditError, LLMError
    from app.modules.ai.llm_purpose_router import PeakHoursBlockedError
    from app.modules.ai.story_spike import StorySpikedError
    from app.modules.crawler.classifier_review_store import enqueue_classifier_review

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
        return composed, None
    except ComposeBusyError:
        enqueue_classifier_review(
            url=url,
            page_text=page_text,
            page_title=page_title,
            category=category,
            storage_score=storage_score,
            metadata={
                "article_id": old_article_id,
                "source": kind or "web",
                "recompose_busy": True,
            },
        )
        return None, {"status": "already_running", "key": COMPOSE_LOCK_KEY}
    except StorySpikedError as spike:
        logger.info(
            "writer spiked recompose of review %s (%s): %s [%s]",
            review_id,
            url,
            spike.category,
            spike.reason,
        )
        _suppress_if_dead_project(url, spike)
        return None, {
            "status": "aborted_by_writer",
            "reason": f"{spike.category}: {spike.reason}",
        }
    except LLMError as exc:
        peak_hours = isinstance(exc, PeakHoursBlockedError)
        credit_issue = isinstance(exc, LLMCreditError)
        status = (
            "skipped_peak_hours"
            if peak_hours
            else ("mistral_credit_insufficient" if credit_issue else "mistral_failed")
        )
        if peak_hours:
            logger.info("recompose of review %s (%s) deferred: %s", review_id, url, exc)
        else:
            logger.error(
                "LLM recompose failed for review %s (%s): %s",
                review_id,
                url,
                exc,
                exc_info=True,
            )
        # Compose failed (or was deferred) — restore the original proposal so the review isn't lost.
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
        return None, {"status": status, "detail": str(exc)[:200]}


def _recompose_resolve_image(
    og_image: str, url: str, service_id: str, composed: ArticleComposeResult
) -> tuple[str, str]:
    """Re-validate a carried-forward og_image, or resolve a fresh one when none survives. A bad image (foreign/stale template artwork) would otherwise survive every subsequent recompose unchanged, since it's never empty and this function only re-resolves when it IS empty. A true share image also becomes the body hero; a brand logo only populates image_url (same split as publish_from_queued_row)."""
    og_image = _validated_hero_checked(og_image, url)
    if og_image:
        return og_image, og_image
    try:
        from app.modules.newspaper.source_image import resolve_article_images

        og, logo = resolve_article_images(
            source_url=url,
            service_id=service_id,
            body=composed.body,
            validate=_validated_hero_checked,
        )
        return og, (og or logo)
    except Exception:
        logger.warning("failed to resolve source images for %s", url, exc_info=True)
        return "", ""


@celery_app.task(
    name="app.tasks.newspaper.recompose_review",
    soft_time_limit=worker_config.COMPOSE_TASK_SOFT_TIME_LIMIT,
    time_limit=worker_config.COMPOSE_TASK_TIME_LIMIT,
)
def recompose_review(review_id: str) -> dict[str, str]:
    """Re-run composition on a pending review's stored source and REPLACE the review with a fresh proposal. Lets an admin watch a previously bad article improve as the writer/grader evolve, without waiting for the source to change. This is a deliberate manual replay, so it bypasses the dedup / novelty / domain gates the normal pipeline applies."""
    import json as _json
    from uuid import UUID

    from app.core.cassandra import get_cassandra_session
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
    row = session.execute(ClassifierReviewStmts.GET_FULL, (rid,)).one()
    if row is None:
        return {"status": "error", "reason": "review_not_found"}
    # This review_id must still be the LIVE, undecided one. Recompose never
    # checked status before — any stale trigger on an already-resolved review
    # (a UI tab that wasn't refreshed after approving/rejecting, a duplicate
    # click, an old review_id from before an earlier recompose) silently
    # yanked an approved-and-queued article back into "pending" and minted
    # yet another review row, even though nothing was actually wrong with it
    # (2026-07-10: KryptoNurd kept "coming back into the classifier" after
    # being approved). Only a genuinely pending review may be recomposed.
    if (row.status or "").strip().lower() != "pending":
        return {
            "status": "error",
            "reason": f"review already resolved (status={row.status!r}) — recompose refused",
        }

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

    composed, compose_error = _recompose_via_writer(
        review_id=review_id,
        url=url,
        page_text=page_text,
        page_title=page_title,
        category=category,
        storage_score=storage_score,
        kind=kind,
        old_article_id=old_article_id,
    )
    if compose_error is not None:
        return compose_error

    og_image, image_field = _recompose_resolve_image(og_image, url, service_id, composed)
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
    # Reuse the previous draft's article_id so a recompose overwrites the same
    # unlisted row in place instead of minting a new one and orphaning the old
    # one — every prior draft never gets deleted, just silently superseded, so
    # each Recompose click otherwise leaves a dangling articles_by_id row that
    # was never in the feed/sitemap but is still directly reachable by URL.
    try:
        reuse_article_id = UUID(old_article_id) if old_article_id else None
    except ValueError:
        reuse_article_id = None
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
        status="on_hold",
        article_id=reuse_article_id,
        image_url=image_field,
        tags=tags,
        prompt_version=getattr(composed, "prompt_version", ""),
    )

    # Grade + deterministic gate, mirroring publish_from_queued_row so the
    # reviewer sees a fresh score and reasons next to the new draft.
    grade_meta, _grade_value, _gate_ok = _grade_and_gate(
        composed,
        title=composed.title,
        source_url=url,
        page_text=page_text,
        service_id=url,
    )

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
            "og_image": image_field,
            "service_id": service_id,
            **grade_meta,
        },
    )

    return {"status": "ok", "review_id": new_review_id, "article_id": article_id}


@celery_app.task(name="app.tasks.newspaper.assign_editorial_brief")
def assign_editorial_brief(brief_id: str) -> dict[str, str]:
    """First-run assignment for an active editorial brief — thin wrapper, see app.modules.newspaper.editorial_assignment for the actual logic."""
    from app.modules.newspaper.editorial_assignment import assign_editorial_brief as _assign

    return _assign(brief_id)


@celery_app.task(name="app.tasks.newspaper.refresh_editorial_brief")
def refresh_editorial_brief(brief_id: str) -> dict[str, str]:
    """Cadence refresh for an editorial brief's linked article — thin wrapper, see app.modules.newspaper.editorial_assignment for the actual logic."""
    from app.modules.newspaper.editorial_assignment import refresh_editorial_brief as _refresh

    return _refresh(brief_id)


@celery_app.task(name="app.tasks.newspaper.scan_editorial_brief_schedule")
def scan_editorial_brief_schedule() -> dict[str, object]:
    """Safety-net beat: assign any active brief still missing its first article, and refresh any brief whose cadence has elapsed."""
    from app.modules.newspaper.editorial_assignment import (
        scan_editorial_brief_schedule as _scan,
    )

    return _scan()


@celery_app.task(name="app.tasks.newspaper.translate_article")
def translate_article_task(
    article_id: str,
    lang: str,
    _english_title: str = "",
    _english_summary: str = "",
    _english_body: str = "",
) -> dict[str, str]:
    """Background task to translate an article into a target language using the LLM.

    Reads the CURRENT article text from the store rather than trusting enqueue-time
    args (kept only for in-flight compatibility with pre-2026-07-05 enqueues), so a
    recompose between enqueue and run can't persist a stale translation. Skips
    languages already stored — re-enqueueing is free.
    """
    import json

    from app.modules.ai.llm_compose import translate_article
    from app.modules.newspaper.article_store import get_article, update_article_translations

    try:
        article = get_article(article_id)
        if article is None or not (article.body or "").strip():
            return {"status": "error", "reason": "article_not_found_or_empty"}
        if lang in (article.translations or {}):
            return {"status": "skipped", "reason": "already_translated", "lang": lang}

        translated = translate_article(
            english_title=article.title or "",
            english_summary=article.summary or "",
            english_body=article.body or "",
            target_language=lang,
        )

        # Store as JSON in the Cassandra map
        translations = {lang: json.dumps(translated, ensure_ascii=False)}

        update_article_translations(article_id, translations)
        try:
            from app.modules.newspaper.indexnow import ping_translation

            ping_translation(article_id, lang, slug=article.slug)
        except Exception:
            logger.warning(
                "IndexNow ping failed for translation %s/%s", article_id, lang, exc_info=True
            )
        return {"status": "ok", "article_id": article_id, "lang": lang}
    except Exception as e:
        logger.error(f"Failed to translate article {article_id} to {lang}: {e}", exc_info=True)
        return {"status": "error", "reason": str(e)}


@celery_app.task(name="app.tasks.newspaper.translate_glossary_term")
def translate_glossary_term_task(slug: str, lang: str) -> dict[str, str]:
    """Background task to translate a published glossary term's term+definition.

    Mirrors translate_article_task's shape: reads the CURRENT row (a term can
    be edited between enqueue and run), skips a language already stored, and
    fails open -- a glossary translation is never on the article's critical
    path.
    """
    import json

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import GlossaryStmts
    from app.modules.newspaper.glossary_translate import translate_glossary_term

    try:
        session = get_cassandra_session()
        row = session.execute(GlossaryStmts.GET_FOR_TRANSLATE, (slug,)).one()
        if row is None or not (row.term or "").strip():
            return {"status": "error", "reason": "term_not_found"}
        existing = dict(row.translations or {})
        if lang in existing:
            return {"status": "skipped", "reason": "already_translated", "lang": lang}

        translated = translate_glossary_term(
            term=row.term or "",
            definition=row.definition or "",
            target_language=lang,
        )
        translations = {lang: json.dumps(translated, ensure_ascii=False)}
        session.execute(GlossaryStmts.UPDATE_TRANSLATIONS, (translations, slug))
        return {"status": "ok", "slug": slug, "lang": lang}
    except Exception as e:
        logger.error(f"Failed to translate glossary term {slug} to {lang}: {e}", exc_info=True)
        return {"status": "error", "reason": str(e)}


def _translate_one_lang_via_deepseek(
    *, english_title: str, english_summary: str, english_body: str, target_language: str
) -> dict[str, str]:
    """One language's translation via DeepSeek instead of the local CPU engines -- see DEEPSEEK_TRANSLATE_LANGS for why some languages route here."""
    from app.core.config import DEEPSEEK_API_BASE, DEEPSEEK_API_KEY, DEEPSEEK_MODEL_TRANSLATE
    from app.modules.ai.llm_compose import translate_article
    from app.modules.ai.llm_openai_compatible import DeepSeekProvider

    client = DeepSeekProvider(
        api_key=DEEPSEEK_API_KEY, api_base=DEEPSEEK_API_BASE, model=DEEPSEEK_MODEL_TRANSLATE
    )
    return translate_article(
        english_title=english_title,
        english_summary=english_summary,
        english_body=english_body,
        target_language=target_language,
        client=client,
    )


def _run_deepseek_translations(
    *,
    article: ArticleDetail,
    article_id: str,
    langs: list[str],
    on_start: Callable[[str], None],
    on_done: Callable[[str, dict[str, str]], None],
    on_error: Callable[[str, str], None],
) -> tuple[list[str], dict[str, str]]:
    """Translate every language in `langs` via DeepSeek, one call each (no load/unload cost to batch, unlike the local engines) -- split out purely to keep translate_article_batch_task's own cyclomatic complexity down."""
    ok: list[str] = []
    failed: dict[str, str] = {}
    for lang in langs:
        on_start(lang)
        try:
            result = _translate_one_lang_via_deepseek(
                english_title=article.title or "",
                english_summary=article.summary or "",
                english_body=article.body or "",
                target_language=lang,
            )
        except Exception:
            logger.error(
                "deepseek translation failed: lang=%s article=%s", lang, article_id, exc_info=True
            )
            failed[lang] = "translation_error"
            on_error(lang, "translation_error")
            continue
        ok.append(lang)
        on_done(lang, result)
    return ok, failed


# Kept registered (not deleted) as a shim: a stale enqueue from before this
# deploy could still reference "app.tasks.newspaper.translate_article" by
# name, and dropping the task definition would make that a hard failure
# instead of a normal (if now-legacy) single-language translation.
@celery_app.task(
    name="app.tasks.newspaper.translate_article_batch",
    # The original 5h50m/6h limits were sized against a 51-minute/41-block
    # measurement (see local_translate_lock.py's TTL comment) that real
    # production data has since disproven: a single content-heavy special
    # edition took 1h41m for 'ps' alone (SeamlessM4T's per-cell beam search)
    # plus ~1h per MiLMMT language, and got SIGKILLed by the 6h hard limit
    # mid-'ru' with 5 of 8 languages never attempted (found 2026-08-08).
    # Widened well past any observed worst case -- on-CPU translation is
    # explicitly latency-tolerant (this platform's whole design), so there
    # is no cost to a generous ceiling beyond bounding genuine hangs.
    # Both engines share this one task's queue (see celery_app.py's
    # task_routes "translate" entry and the dedicated
    # algorand-platform-celery-translate systemd unit), so overriding here
    # rather than the app-wide task_soft_time_limit/task_time_limit leaves
    # every OTHER task's limits untouched.
    soft_time_limit=57600,  # 16h
    time_limit=59400,  # 16h30m hard kill
    # Root-caused live 2026-08-20/21: with Celery's default early-ack, a
    # message is removed from the broker the MOMENT a worker picks it up --
    # before any translation work happens. When the pre-restart worker
    # (running stale code against the reworked local_translate.py) picked up
    # a translation batch and simply couldn't execute it, the task vanished
    # with zero trace: gone from the queue, zero languages persisted, no
    # failure result anywhere. ~52 articles' translation batches were lost
    # this way over four days with nothing ever retrying them. The systemd
    # unit's own comment already assumed this couldn't happen ("a killed
    # batch loses at most the one language in flight... re-enqueueing skips
    # everything already stored") -- true for what gets PERSISTED per
    # language, but that safety net only protects work already in progress
    # on this specific delivery, not the task's presence in the queue at
    # all. acks_late=True defers the ack until the task actually returns (or
    # is confirmed lost), so a worker that dies/hangs/can't-execute mid-task
    # gets its in-flight message redelivered to the next worker instead of
    # silently discarding it. Safe here specifically because the task is
    # already idempotent (re-checks which languages are still missing before
    # doing any work) -- a redelivered task just skips what a prior attempt
    # already persisted. reject_on_worker_lost=True makes a hard SIGKILL
    # (e.g. the 16h30m time_limit firing) explicitly requeue rather than
    # risk the message sitting unacked in limbo if the connection drop isn't
    # detected cleanly.
    acks_late=True,
    reject_on_worker_lost=True,
)
def translate_article_batch_task(article_id: str, langs: list[str]) -> dict:
    """Background task to translate an article into every language in `langs`.

    Same freshness/idempotency guards as the retired per-language task:
    re-reads the CURRENT article from the store (a recompose between enqueue
    and run must not persist a stale translation) and re-checks each lang
    against what's already stored (time passes in a queue -- a manual
    backfill run could have filled one in the meantime).

    Languages in DEEPSEEK_TRANSLATE_LANGS translate via DeepSeek (one call
    each, no batching needed -- an API call has none of the local engines'
    load/unload cost); everything else still goes through
    app.modules.ai.local_translate.translate_article_batch's engine
    grouping / load-once / explicit-unload logic.
    """
    import json

    from app.core.config import DEEPSEEK_TRANSLATE_LANGS
    from app.modules.ai.local_translate import translate_article_batch
    from app.modules.ai.translation_session_store import (
        finish_translation_session,
        start_translation_session,
    )
    from app.modules.newspaper.article_store import get_article, update_article_translations

    try:
        article = get_article(article_id)
        if article is None or not (article.body or "").strip():
            return {"status": "error", "reason": "article_not_found_or_empty"}
        existing = set((article.translations or {}).keys())
        pending = [lang for lang in langs if lang not in existing]
        if not pending:
            return {"status": "skipped", "reason": "already_translated", "langs": langs}

        # Keyed by lang rather than passed around explicitly: on_language_start
        # fires from inside translate_article_batch's own loop, so this is the
        # only way the later done/error callback for the SAME lang knows which
        # translation_sessions row to close out.
        session_refs: dict[str, tuple] = {}

        def _on_start(lang: str) -> None:
            session_refs[lang] = start_translation_session(article_id, lang)

        def _persist(lang: str, result: dict[str, str]) -> None:
            update_article_translations(article_id, {lang: json.dumps(result, ensure_ascii=False)})
            finish_translation_session(session_refs.get(lang), status="ok")
            # Push the newly-landed language into Typesense so site search
            # can actually find it. Before this, a translation only ever
            # reached Cassandra + IndexNow -- Typesense's articles collection
            # had no per-language fields at all, so a reader searching in
            # French only ever matched (and only ever saw) English content.
            try:
                from app.modules.search.core.indexer import upsert_article_translation

                upsert_article_translation(
                    article_id=article_id,
                    lang=lang,
                    title=result.get("title", ""),
                    summary=result.get("summary", ""),
                    body=result.get("body", ""),
                )
            except Exception:
                logger.warning(
                    "Typesense translation index failed for %s/%s", article_id, lang, exc_info=True
                )
            try:
                from app.modules.newspaper.indexnow import ping_translation

                ping_translation(article_id, lang, slug=article.slug)
            except Exception:
                logger.warning(
                    "IndexNow ping failed for translation %s/%s", article_id, lang, exc_info=True
                )

        def _on_error(lang: str, reason: str) -> None:
            finish_translation_session(session_refs.get(lang), status="error", error=reason)

        deepseek_pending = [lang for lang in pending if lang in DEEPSEEK_TRANSLATE_LANGS]
        local_pending = [lang for lang in pending if lang not in DEEPSEEK_TRANSLATE_LANGS]

        ok, failed = _run_deepseek_translations(
            article=article,
            article_id=article_id,
            langs=deepseek_pending,
            on_start=_on_start,
            on_done=_persist,
            on_error=_on_error,
        )

        if local_pending:
            outcome = translate_article_batch(
                english_title=article.title or "",
                english_summary=article.summary or "",
                english_body=article.body or "",
                target_languages=local_pending,
                on_language_start=_on_start,
                on_language_done=_persist,
                on_language_error=_on_error,
            )
            ok.extend(outcome["ok"])
            failed.update(outcome["failed"])

        status = "ok" if not failed else "partial"
        return {"status": status, "article_id": article_id, "ok": ok, "failed": failed}
    except Exception as e:
        logger.error(f"Failed to batch-translate article {article_id}: {e}", exc_info=True)
        return {"status": "error", "reason": str(e)}


@celery_app.task(name="app.tasks.newspaper.backfill_article_translations")
def backfill_article_translations_task(limit: int = 500) -> dict:
    """Queue missing translations for feed-visible articles (e.g. after adding fa/ps)."""
    from app.modules.newspaper.article_store import list_feed_articles

    articles_touched = 0
    tasks_queued = 0
    for row in list_feed_articles(limit=limit):
        n = enqueue_missing_article_translations(str(row.article_id))
        if n:
            articles_touched += 1
            tasks_queued += n
    return {
        "limit": limit,
        "articles": articles_touched,
        "tasks_queued": tasks_queued,
    }


def _recompose_published_source_text(
    existing: object, service_id: str, source_url: str
) -> tuple[str, str, str]:
    """Fresh source text for an archive refresh: a live re-scrape of the source page when there is one, aggregated into the service's other already-crawled pages (same corpus the discovery path composes from — its previous omission caused a 2026-07-14 incident where a recompose kept missing a page a prior crawl had already found). Falls back to the article's own stored body when there's no page to re-scrape or the scrape/aggregation fails. Returns (page_text, page_title, scraped_og)."""
    from app.core import config as worker_config

    page_text = existing.body or ""
    page_title = existing.title or ""
    scraped_og = ""
    if source_url.lower().startswith(("http://", "https://", "browser://")):
        try:
            result = get_scraper_for_url(source_url).scrape(
                url=source_url, source_id=service_id or source_url
            )
            if (result.text or "").strip():
                page_text = result.text
                page_title = result.title or page_title
                scraped_og = getattr(result, "og_image", "") or ""
        except Exception:
            logger.warning(
                "recompose_published: fresh scrape failed for %s — composing from stored body",
                source_url,
                exc_info=True,
            )

    is_web_source = bool(source_url) and _source_kind_from_url(source_url) == "web"
    if is_web_source and worker_config.SERVICE_CONTEXT_ENABLED:
        try:
            from app.modules.newspaper.service_context import build_service_context

            page_text = build_service_context(
                service_id=service_id or source_url,
                display_name=service_id,
                entry_url=source_url,
                entry_title=page_title,
                entry_text=page_text,
            )
        except Exception:
            logger.warning(
                "recompose_published: service context aggregation failed for %s — "
                "using entry page only",
                source_url,
                exc_info=True,
            )
    return page_text, page_title, scraped_og


def _recompose_published_compose(
    self: Task,
    *,
    article_id: str,
    service_id: str,
    source_url: str,
    page_text: str,
    page_title: str,
    brief_for_recompose: EditorialBrief | None,
) -> tuple[ArticleComposeResult | None, dict[str, str] | None]:
    """Compose the archive-refresh draft. Recomposes from the ORIGINAL INPUT (the brief body, when this article came from an editorial brief) rather than the prior article's own OUTPUT — handing the writer its own previous body as "source material" just re-launders whatever was in it, including a wrong premise (Pera Wallet incident 2026-07-20: two recomposes kept declaring Pera defunct because the prior draft said so, never re-checking). Returns (composed, None) on success, or (None, error_response) on a writer spike or LLM failure; raises via self.retry on a busy compose lock."""
    from app.modules.ai.llm_provider import LLMCreditError, LLMError
    from app.modules.ai.llm_purpose_router import PeakHoursBlockedError
    from app.modules.ai.story_spike import StorySpikedError

    try:
        if brief_for_recompose is not None:
            composed = compose_scrape_article(
                service_name=service_id or "editorial",
                source_url=source_url,
                page_title=brief_for_recompose.title,
                page_text=brief_for_recompose.body_markdown,
                keywords=brief_for_recompose.keywords,
                brief_id=brief_for_recompose.brief_id,
                txid=f"recompose-{article_id[:12]}",
                round_num=0,
                diff=None,
                is_first_snapshot=True,
                publish_kind=PublishKind.SERVICE_DISCOVERY,
                publish_topic=PublishTopic.EDITORIAL_ASSIGNMENT,
                is_special_edition=getattr(brief_for_recompose, "is_special_edition", False),
            )
        else:
            composed = compose_scrape_article(
                service_name=service_id or source_url or "archive",
                source_url=source_url or f"article:{article_id}",
                page_title=page_title,
                page_text=page_text,
                txid=f"recompose-{article_id[:12]}",
                round_num=0,
                diff=None,
                is_first_snapshot=True,
                publish_kind=PublishKind.SERVICE_DISCOVERY,
                publish_topic=PublishTopic.GENERIC,
                # Root-caused live 2026-08-17: with neither first_coverage nor a
                # real diff, an archive-refresh recompose gets NEITHER
                # FIRST_COVERAGE_GUIDANCE's "give a comprehensive picture" nor
                # EVOLUTION_GUIDANCE's "lead with the change" -- it's an
                # unaugmented compose that has no explicit instruction to be
                # comprehensive, so it naturally gravitates toward whatever
                # feels newest/most-emphasized in the source material (a
                # Downbad.farm recompose fetched material on the site's full
                # feature set but wrote almost the entire piece about one
                # newly-previewed feature). This is exactly the "give a
                # complete, standalone picture, don't center on one recent
                # detail" instruction an archive refresh needs -- same as a
                # genuinely first-time service compose, even though the
                # service technically has prior coverage (the guidance text
                # itself no longer claims otherwise, see its own docstring).
                first_coverage=True,
            )
        return composed, None
    except ComposeBusyError as exc:
        # The global compose lock is held (a drain compose, or a sibling
        # archive-refresh task — the worker runs concurrency=4, so batched
        # recomposes DO collide). A plain return here silently dropped the
        # whole recompose; retry with backoff instead until the lock frees.
        raise self.retry(exc=exc, countdown=180) from exc
    except StorySpikedError as spike:
        logger.info(
            "writer spiked archive-refresh of %s: %s [%s]",
            article_id,
            spike.category,
            spike.reason,
        )
        return None, {
            "status": "aborted_by_writer",
            "reason": f"{spike.category}: {spike.reason}",
        }
    except LLMError as exc:
        if isinstance(exc, PeakHoursBlockedError):
            logger.info("recompose_published deferred for %s: %s", article_id, exc)
            return None, {"status": "skipped_peak_hours", "detail": str(exc)[:200]}
        credit_issue = isinstance(exc, LLMCreditError)
        status = "mistral_credit_insufficient" if credit_issue else "mistral_failed"
        logger.error("recompose_published failed for %s: %s", article_id, exc, exc_info=True)
        return None, {"status": status, "detail": str(exc)[:200]}


def _recompose_published_hero_image(
    scraped_og: str, source_url: str, article_id: str
) -> tuple[str, str]:
    """Hero: fresh og when the re-scrape produced one, else the live article's current art (never downgrade a working hero to nothing). 2026-08-24: reads `articles` directly (was `articles_by_id`)."""
    from uuid import UUID

    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    og_image = _validated_hero_checked(scraped_og, source_url)
    if og_image:
        return og_image, og_image
    try:
        row = get_cassandra_session().execute(ArticlesStmts.GET_FULL_BY_ID, (UUID(article_id),)).one()
        return og_image, ((row.image_url or "") if row else "")
    except Exception:
        return og_image, ""


@celery_app.task(
    name="app.tasks.newspaper.recompose_published",
    bind=True,
    max_retries=20,
    soft_time_limit=worker_config.COMPOSE_TASK_SOFT_TIME_LIMIT,
    time_limit=worker_config.COMPOSE_TASK_TIME_LIMIT,
)
def recompose_published(
    self: Task, article_id: str, *, extra_source_material: str = ""
) -> dict[str, str]:
    """Archive refresh: re-compose a PUBLISHED article into a NEW unlisted draft. When the draft clears the (strict) RECOMPOSE_AUTO_APPLY bar — grade, headline style, gatekeeper — it swaps onto the live article_id immediately (autonomous mode); otherwise it holds in the review queue for a human. Either way apply_recomposed_article does the swap: the URL survives and published_at is re-stamped to the apply time (recompose is a re-publish — owner policy 2026-07-15 — the story returns to the top of the feed).

    recompose_review cannot serve this case: it reuses the article_id at
    compose time, which would replace the live page before any approval
    (human or automatic) — and approving it would double-publish the feed row.

    ``extra_source_material`` (2026-08-17, source-URL-dedup cleanup): optional
    extra text folded into the source material below the live scrape, clearly
    labeled as retiring/superseded coverage. Added specifically for a batch
    that consolidates several old per-service articles down to one fresh one
    and deletes the rest -- without this, the writer only ever sees the
    CURRENT live page, never the content of the sibling articles about to be
    deleted, so a fact that existed only in an older article (not reflected
    on the live page today) would be silently lost the moment its row is
    deleted. Empty by default -- every other caller (admin "Recompose"
    click, recompose_session_service, the weekly cadence) is unaffected.
    """
    from app.modules.crawler.classifier_review_store import (
        enqueue_classifier_review,
        has_pending_review_for_url,
    )
    from app.modules.newspaper.article_store import get_article, insert_stored_article
    from app.modules.newspaper.security import sanitize_body

    existing = get_article(article_id)
    if existing is None:
        return {"status": "error", "reason": "article_not_found"}
    service_id = existing.service_id or ""
    source_url = (existing.source_url or "").strip()

    # Same "a pending review already covers this" veto the normal pipeline
    # applies before a compose (_pending_review_veto) -- recompose_published
    # never had it, so every manual/API re-trigger (admin "Recompose" click,
    # recompose_archive.py, recompose_session_service) while a prior draft
    # was still awaiting review just paid for another full compose and left
    # yet another orphaned unlisted draft behind (root-caused 2026-08-13:
    # 10 accumulated orphan drafts for one article, none ever applied,
    # nothing ever pointed a caller at the one already pending).
    review_url_key = source_url or f"article:{article_id}"
    try:
        pending = has_pending_review_for_url(review_url_key)
    except Exception:
        # Fail OPEN: an infra hiccup on this check must never block a
        # legitimate recompose -- worst case reverts to the pre-2026-08-13
        # behavior of one extra compose, not a stuck task.
        pending = False
    if pending:
        return {"status": "duplicate_review_pending", "article_id": article_id}

    # Fresh source when the article has a real page behind it; the article's
    # own prose otherwise (editorial briefs, chain triggers) — the two-stage
    # writer re-researches with tools either way, so this is a starting point,
    # not the ceiling.
    page_text, page_title, scraped_og = _recompose_published_source_text(
        existing, service_id, source_url
    )
    if extra_source_material.strip():
        page_text = (
            f"{page_text}\n\n"
            "## RETIRING PRIOR COVERAGE (these older articles about this same "
            "service are being deleted once this recompose lands -- if any "
            "carries a genuine fact that is no longer visible on the live page "
            "above, work it in; do not just restate old headline numbers the "
            "live page has since superseded):\n\n"
            f"{extra_source_material.strip()}"
        )

    # Recompose from the ORIGINAL INPUT, not the prior OUTPUT. An editorial-brief
    # article has no page to re-scrape, so the generic path below would hand the
    # writer its OWN previous article body as "source material" and it just
    # re-launders whatever was in it — including a wrong premise (Pera Wallet
    # incident 2026-07-20: two recomposes kept declaring Pera defunct because the
    # prior draft said so, never re-checking). Re-run the assignment from the
    # brief itself so the writer researches the topic from scratch.
    brief_for_recompose = None
    if source_url.lower().startswith("editorial://brief/"):
        from app.modules.newspaper.editorial_assignment import get_brief

        brief_for_recompose = get_brief(source_url.rsplit("/", 1)[-1])

    from app.modules.ai.writer_tools import recomposing_article

    with recomposing_article(article_id):
        composed, compose_error = _recompose_published_compose(
            self,
            article_id=article_id,
            service_id=service_id,
            source_url=source_url,
            page_text=page_text,
            page_title=page_title,
            brief_for_recompose=brief_for_recompose,
        )
    if compose_error is not None:
        return compose_error

    # Hero: fresh og when the re-scrape produced one, else the live article's
    # current art (never downgrade a working hero to nothing).
    og_image, image_field = _recompose_published_hero_image(scraped_og, source_url, article_id)

    kind = _source_kind_from_url(source_url) if source_url else None
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
    draft_id, _ = insert_stored_article(
        service_id=service_id,
        title=composed.title,
        summary=composed.summary,
        body=_with_hero_image(
            sanitize_body(composed.body), og_image, composed.title, source_url=source_url
        ),
        trigger_txid=f"recompose-{article_id[:12]}",
        trigger_round=0,
        source_url=source_url,
        publish_to_feed=False,
        image_url=image_field,
        tags=tags,
        prompt_version=getattr(composed, "prompt_version", ""),
    )

    # Autonomous mode (owner decision, 2026-07-12): swap the draft onto the
    # live article without a human click when EVERY signal clears a bar
    # stricter than fresh-article auto-publish — this overwrites a page
    # that's already public, so any missing/errored signal (grade, gate) or
    # unmet check fails CLOSED to manual review, never open.
    grade_meta, grade_value, gate_ok = _grade_and_gate(
        composed,
        title=composed.title,
        source_url=source_url,
        page_text=page_text,
        service_id=source_url or f"article:{article_id}",
        label=article_id,
    )

    from app.core import config as worker_config
    from app.modules.newspaper.article_grader import headline_violations

    auto_apply = (
        worker_config.RECOMPOSE_AUTO_APPLY_ENABLED
        and grade_value is not None
        and grade_value >= worker_config.RECOMPOSE_AUTO_APPLY_GRADE_FLOOR
        and not headline_violations(composed.title)
        and gate_ok
    )

    review_id = enqueue_classifier_review(
        url=source_url or f"article:{article_id}",
        page_text=page_text,
        page_title=page_title or composed.title,
        category="",
        storage_score=0.0,
        metadata={
            "article_id": draft_id,
            "replaces_article_id": article_id,
            "og_image": image_field,
            "service_id": service_id,
            "source": "recompose_published",
            "auto_applied": "1" if auto_apply else "0",
            **grade_meta,
        },
    )

    if auto_apply:
        from app.modules.crawler.classifier_review_store import complete_classifier_review

        # Resolve the review immediately (audit trail stays visible in admin
        # as "auto_approved") rather than leaving it pending for a click that
        # will never come. Never written to classifier_feedback — that table
        # trains the classifier from HUMAN labels; an auto-decision isn't one,
        # and feeding it back in would let the model approve its own homework.
        complete_classifier_review(review_id, resolution="auto_approved")
        apply_result = apply_recomposed_article(draft_id, article_id)
        return {
            "status": "auto_applied",
            "review_id": review_id,
            "draft_article_id": draft_id,
            "apply_result": apply_result.get("status", "unknown"),
        }

    return {"status": "ok", "review_id": review_id, "draft_article_id": draft_id}


@celery_app.task(
    name="app.tasks.newspaper.recompose_session_service",
    soft_time_limit=worker_config.COMPOSE_TASK_SOFT_TIME_LIMIT,
    time_limit=worker_config.COMPOSE_TASK_TIME_LIMIT,
)
def recompose_session_service(source_url: str) -> dict[str, str]:
    """Admin-triggered from the Sessions tab: "I just read this session's transcript, changed a prompt, and want to see the pipeline behave now" -- resolves the live article behind this source and hands off to recompose_published, the SAME archive-refresh path used for the pipeline's own weekly recompose cadence.

    Takes source_url, NOT compose_sessions.service_id -- root-caused
    2026-08-05 live: for a scraped-page compose, service_id is deliberately
    set to the raw source_url itself (see tool_context in
    _compose_via_writer_tools_locked), not a normalized service_id slug, so
    find_latest_service_article(session.service_id) never matched anything.
    Two real resolution paths from source_url:
    - editorial://brief/<id> -> the brief's own linked_article_id (no
      domain/service concept applies to an editorial assignment).
    - a real page URL -> domain -> service_for_domain -> service_id ->
      find_latest_service_article (the same chain service_for_domain/
      find_latest_service_article already serve elsewhere).
    A compose session has no article_id of its own (compose_sessions and
    articles_by_id are separate tables) and, by the time someone is reading
    it in the Sessions tab, its originating publish_queue row has almost
    always already resolved -- recompose_session_service (unlike
    compose_queue_row_now) never touches the queue at all.
    """
    source_url = (source_url or "").strip()
    if not source_url:
        return {"status": "error", "reason": "no_source_url"}

    article_id: str | None = None
    if source_url.lower().startswith("editorial://brief/"):
        from app.modules.newspaper.editorial_assignment import get_brief

        brief = get_brief(source_url.rsplit("/", 1)[-1])
        article_id = brief.linked_article_id if brief else None
    else:
        from urllib.parse import urlparse

        from app.modules.crawler.domain_tracker import domain_from_url
        from app.modules.newspaper.article_matching import find_latest_service_article
        from app.modules.newspaper.service_sources import service_for_domain

        # service_by_domain is keyed by whatever a source's own registration
        # call passed as its "domain" -- in practice that's often the full
        # host (museum.datahistory.org), not domain_from_url's deliberately
        # subdomain-collapsed eTLD+1 (datahistory.org). Root-caused live
        # 2026-08-07: an admin recompose of the Data History Museum article
        # silently no-op'd (fast "no_live_article_for_source", no compose
        # ever started) because domain_from_url stripped exactly the
        # subdomain the lookup needed. Try the full host first -- it's what
        # a service is actually registered under far more often than not --
        # and fall back to the collapsed eTLD+1 for services registered at
        # their root domain.
        host = (urlparse(source_url).hostname or "").lower()
        service_id = service_for_domain(host) if host else ""
        if not service_id:
            domain = domain_from_url(source_url)
            service_id = service_for_domain(domain) if domain else ""
        if service_id:
            article_id = find_latest_service_article(service_id)

    if not article_id:
        return {"status": "error", "reason": "no_live_article_for_source"}
    return recompose_published(article_id)


@celery_app.task(name="app.tasks.newspaper.apply_recomposed_article")
def apply_recomposed_article(draft_article_id: str, live_article_id: str) -> dict[str, str]:
    """Approved recompose of a published article: swap the draft's content onto the live article_id (same URL; published_at re-stamped to the apply time — recompose is a re-publish, owner policy 2026-07-15 — so the story returns to the top of the feed), version both states, re-index, re-translate, ping IndexNow. The unlisted draft row is left behind (same convention as recompose_review's superseded drafts — never in the feed or sitemap).

    DRAFT GUARD (2026-08-11): when live_article_id is itself a drafted
    (admin-withdrawn) article, none of that publish-adjacent fanout may
    run — Typesense indexing and an IndexNow ping would actively surface a
    withdrawn article in search, exactly what draft status exists to
    prevent. Content still updates (replace_article_content handles that
    unconditionally, see its own draft guard); only the indexing/
    translation/IndexNow side effects below are skipped.
    """
    import time as _time
    from uuid import UUID as _UUID

    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session
    from app.modules.newspaper.article_store import get_article, replace_article_content
    from app.modules.newspaper.article_version_store import save_article_version

    draft = get_article(draft_article_id)
    live = get_article(live_article_id)
    if draft is None or live is None:
        return {"status": "error", "reason": "draft_or_live_missing"}

    # 2026-08-24: reads `articles` directly (was `articles_by_id`) -- "draft"
    # is now status == 'draft' rather than a separate boolean column.
    session = get_cassandra_session()
    live_row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (_UUID(live_article_id),)).one()
    live_is_drafted = bool(live_row and live_row.status == "draft")
    draft_row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (_UUID(draft_article_id),)).one()
    tags = list(draft_row.tags or []) if draft_row else []
    if "updated" not in {t.lower() for t in tags}:
        tags = [*tags, "updated"]
    image_url = (draft_row.image_url or "") if draft_row else ""

    save_article_version(
        article_id=live_article_id,
        title=live.title,
        summary=live.summary,
        body=live.body,
        edit_reason="before_recompose_published",
        editor="system",
    )
    new_published_at = replace_article_content(
        article_id=live_article_id,
        title=draft.title,
        summary=draft.summary,
        body=draft.body,
        tags=tags,
        image_url=image_url,
    )
    if not new_published_at:
        return {"status": "error", "reason": "replace_failed"}
    save_article_version(
        article_id=live_article_id,
        title=draft.title,
        summary=draft.summary,
        body=draft.body,
        edit_reason=f"recompose_published:{draft_article_id[:12]}",
        editor="recompose",
    )

    if live_is_drafted:
        logger.info(
            "apply_recomposed_article: %s is a draft — content updated, "
            "index/translate/IndexNow fanout skipped",
            live_article_id,
        )
        return {"status": "ok_draft_preserved", "article_id": live_article_id}

    index_article.delay(
        article_id=live_article_id,
        title=draft.title,
        summary=draft.summary,
        body=draft.body,
        service_id=live.service_id,
        published_at_epoch=int(new_published_at.timestamp()) or int(_time.time()),
    )
    # Translations were cleared with the old prose; re-enqueue all languages.
    enqueue_article_translations(live_article_id)
    try:
        from app.modules.newspaper.indexnow import ping_article

        ping_article(live_article_id, translation_langs=[], slug=live.slug)
    except Exception:
        pass
    return {"status": "ok", "article_id": live_article_id}
